"""The agent's outbound-only WebSocket.

Generators never accept inbound connections, which is what lets them run in a
locked-down network. Commands travel the other way down this same socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from plimsoll_api import storage
from plimsoll_api.db.session import session_for_org
from plimsoll_api.messaging import ERRORS_KIND, METRICS_INGESTION, get_bus, run_channel
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.tokens import AgentClaims, TokenError, decode_agent_token
from plimsoll_api.services import credentials
from plimsoll_contracts.agent import (
    Accepted,
    ArtifactUrl,
    ArtifactUrlRequest,
    Command,
    CommandFrame,
    ErrorsFrame,
    HeartbeatAck,
    MetricsFrame,
    Registered,
    StateReport,
)
from plimsoll_contracts.runs import RunStatus

router = APIRouter()

COMMAND_FOR_STATUS = {
    RunStatus.QUEUED: Command.WAIT,
    RunStatus.ALLOCATING: Command.WAIT,
    RunStatus.STARTING: Command.WAIT,
    RunStatus.RUNNING: Command.START,
    RunStatus.STOPPING: Command.STOP,
    RunStatus.COMPLETED: Command.STOP,
    RunStatus.FAILED: Command.CANCEL,
    RunStatus.CANCELLED: Command.CANCEL,
}


def _claims(websocket: WebSocket, run_id: uuid.UUID) -> AgentClaims | None:
    header = websocket.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        claims = decode_agent_token(header.removeprefix("Bearer "))
    except TokenError:
        return None
    # The token names its run. A token for another run is not a token for this
    # one, however valid its signature.
    return claims if claims.run_id == run_id else None


def _command_for(status: str) -> Command:
    return COMMAND_FOR_STATUS.get(RunStatus(status), Command.WAIT)


async def _desired(claims: AgentClaims) -> tuple[str, Command]:
    async with session_for_org(claims.organization_id) as session:
        run = await repo.get(session, claims.run_id)
    if run is None:
        return RunStatus.CANCELLED, Command.CANCEL
    return run.status, _command_for(run.status)


@router.websocket("/api/v1/agent/runs/{run_id}")
async def agent_channel(websocket: WebSocket, run_id: uuid.UUID) -> None:
    claims = _claims(websocket, run_id)
    if claims is None:
        # Refused before the upgrade: an unauthenticated socket never opens.
        await websocket.close(code=4401)
        return
    await websocket.accept()

    pushes: asyncio.Task[None] | None = None
    try:
        while True:
            raw = await websocket.receive_json()
            kind = raw.get("type")

            if kind == "register":
                # The frame's own ordinal is not read: the token says which
                # generator this is, and the token is the only authority here.
                async with session_for_org(claims.organization_id) as session:
                    await repo.set_generator_status(
                        session, claims.run_id, claims.ordinal, "REGISTERED", heartbeat=True
                    )
                    run = await repo.get(session, claims.run_id)
                    generators = await repo.generators_for(session, claims.run_id)
                if run is None:
                    await websocket.close(code=4404)
                    return
                mine = next(g for g in generators if g.ordinal == claims.ordinal)
                snapshot = run.configuration_snapshot
                workload = snapshot["workload"]
                plan = snapshot["plans"][0]
                # Decrypted here and held only as long as the frame takes to
                # send. The agent keeps them in memory and never writes them.
                async with session_for_org(claims.organization_id) as session:
                    resolved = await credentials.variables(session)
                await websocket.send_text(
                    Registered(
                        desired_state=run.status,
                        command=_command_for(run.status),
                        assigned_users=mine.assigned_users,
                        duration_seconds=workload["durationSeconds"],
                        ramp_up_seconds=workload["rampUpSeconds"],
                        plan_path=plan["planPath"],
                        # Signed per registration rather than stored: a
                        # presigned URL expires and configuration does not.
                        bundle_url=storage.presign_get(
                            storage.bundle_key(claims.run_id),
                            seconds=int(workload["durationSeconds"]) + 600,
                        ),
                        bundle_sha256=snapshot.get("bundleSha256", ""),
                        allowlist=snapshot.get("allowlist", []),
                        variables=resolved,
                    ).model_dump_json(by_alias=True)
                )
                if pushes is None:
                    pushes = asyncio.create_task(_push_commands(websocket, claims))

            elif kind == "heartbeat":
                async with session_for_org(claims.organization_id) as session:
                    await repo.touch_heartbeat(session, claims.run_id, claims.ordinal)
                desired, command = await _desired(claims)
                await websocket.send_text(
                    HeartbeatAck(desired_state=desired, command=command).model_dump_json(
                        by_alias=True
                    )
                )

            elif kind == "metrics":
                frame = MetricsFrame.model_validate(raw)
                for window in frame.windows:
                    # The run and the organisation come from the token, never
                    # from the payload: an agent is outside the trust boundary,
                    # and a tenant identifier it supplied would be an
                    # authorisation bypass on the hypertable.
                    await get_bus().publish(
                        METRICS_INGESTION,
                        {
                            **window,
                            "runId": str(claims.run_id),
                            "ordinal": str(claims.ordinal),
                            "organizationId": str(claims.organization_id),
                        },
                    )
                await websocket.send_text(Accepted().model_dump_json())

            elif kind == "errors":
                faults = ErrorsFrame.model_validate(raw)
                for group in faults.groups:
                    await get_bus().publish(
                        METRICS_INGESTION,
                        {
                            **group,
                            "kind": ERRORS_KIND,
                            "runId": str(claims.run_id),
                            "organizationId": str(claims.organization_id),
                        },
                    )
                await websocket.send_text(Accepted().model_dump_json())

            elif kind == "artifact_url_request":
                request = ArtifactUrlRequest.model_validate(raw)
                try:
                    # Built from the token's own run and ordinal, never from
                    # anything the agent sends: an agent cannot write into
                    # another run's prefix, or another generator's.
                    key = storage.artifact_key(claims.run_id, claims.ordinal, request.name)
                except ValueError:
                    # The agent names the artifact; the control plane decides
                    # where it lands, and refuses a name that tries to choose.
                    await websocket.send_text(Accepted().model_dump_json())
                    continue
                await websocket.send_text(
                    ArtifactUrl(name=request.name, url=storage.presign_put(key)).model_dump_json(
                        by_alias=True
                    )
                )

            elif kind == "state":
                report = StateReport.model_validate(raw)
                async with session_for_org(claims.organization_id) as session:
                    await repo.set_generator_status(
                        session, claims.run_id, claims.ordinal, report.state, heartbeat=True
                    )
                await websocket.send_text(Accepted().model_dump_json())

    except WebSocketDisconnect:
        # A disconnect is not a failure. The generator is judged by its
        # heartbeat, so silence is what marks it lost, not a closed socket.
        pass
    finally:
        if pushes is not None:
            pushes.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pushes


async def _push_commands(websocket: WebSocket, claims: AgentClaims) -> None:
    """Announcements are a nudge, not the truth.

    The heartbeat acknowledgement carries the desired state too, so a push that
    never arrives costs one interval rather than the run.
    """
    async with get_bus().listen(run_channel(claims.run_id)) as messages:
        async for message in messages:
            await websocket.send_text(
                CommandFrame(command=Command(message["command"])).model_dump_json()
            )
