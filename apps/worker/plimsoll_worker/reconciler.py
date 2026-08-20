"""What the run needs next, decided from persisted state alone.

The worker is a reconciler rather than a script, and this function is why: a
duplicate delivery, a restarted worker, and a timer tick all arrive here, and
all of them are answered from the same picture. Nothing important lives in
worker memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto

from plimsoll_contracts.runs import (
    TERMINAL_GENERATOR_STATUSES,
    TERMINAL_RUN_STATUSES,
    GeneratorStatus,
    RunStatus,
)

HEARTBEAT_TIMEOUT = timedelta(seconds=35)

# What counts as ready to start: at the line, or already past it.
STARTED_OR_READY = frozenset({GeneratorStatus.READY, GeneratorStatus.RUNNING})


class Decision(Enum):
    PROVISION = auto()
    START = auto()
    MARK_LOST = auto()
    CONTINUE_DEGRADED = auto()
    FAIL = auto()
    FINISH = auto()
    WAIT = auto()
    DONE = auto()


@dataclass(frozen=True)
class GeneratorRow:
    ordinal: int
    status: str
    assigned_users: int
    external_ref: str | None
    last_heartbeat: datetime | None


@dataclass(frozen=True)
class RunView:
    status: str
    generators: list[GeneratorRow]
    now: datetime
    duration_seconds: int
    started_at: datetime | None = None
    max_capacity_loss_percent: int = 10


def is_silent(generator: GeneratorRow, now: datetime) -> bool:
    """Silence is what marks a generator lost -- not a closed socket, which a
    healthy agent may reopen. Shared with the applying code so that the row the
    decision was made about is the row that gets written."""
    return (
        generator.status not in TERMINAL_GENERATOR_STATUSES
        and generator.last_heartbeat is not None
        and now - generator.last_heartbeat > HEARTBEAT_TIMEOUT
    )


def _lost_fraction(view: RunView) -> float:
    planned = sum(generator.assigned_users for generator in view.generators)
    if planned == 0:
        return 1.0
    lost = sum(
        generator.assigned_users
        for generator in view.generators
        if generator.status in (GeneratorStatus.LOST, GeneratorStatus.FAILED)
    )
    return lost / planned


def decide(view: RunView) -> Decision:
    if view.status in TERMINAL_RUN_STATUSES:
        return Decision.DONE

    if view.status == RunStatus.QUEUED:
        return Decision.PROVISION

    # A run that reached ALLOCATING and still has an ordinal without a
    # container was abandoned part-way through provisioning -- a worker that
    # died between creating containers and recording them, or before creating
    # them at all. Nothing else moves an ALLOCATING run on, so waiting would
    # leave it there for the life of the deployment while any containers it did
    # make ran unattended. Provisioning is safe to re-enter: an ordinal that
    # already has one is skipped, and one whose container exists but was never
    # recorded is adopted rather than made twice.
    if view.status == RunStatus.ALLOCATING and any(
        generator.external_ref is None for generator in view.generators
    ):
        return Decision.PROVISION

    if any(is_silent(generator, view.now) for generator in view.generators):
        return Decision.MARK_LOST

    lost = _lost_fraction(view)
    if lost > 0:
        # Capacity loss is judged as a percentage of planned virtual users, so
        # the same absolute loss means different things to different runs.
        if lost * 100 >= view.max_capacity_loss_percent:
            return Decision.FAIL
        if view.status == RunStatus.RUNNING:
            return Decision.CONTINUE_DEGRADED

    live = [
        generator
        for generator in view.generators
        if generator.status not in TERMINAL_GENERATOR_STATUSES
    ]
    if not live:
        return Decision.FINISH

    # Ready or beyond. A generator that is already RUNNING got its start from
    # an earlier incarnation of this run -- its container survived a worker
    # that did not -- and requiring exactly READY would wait for a state it has
    # already passed. Short of ready still waits, which is what keeps a
    # staggered start from smearing the ramp.
    if view.status == RunStatus.STARTING and all(
        generator.status in STARTED_OR_READY for generator in live
    ):
        return Decision.START

    return Decision.WAIT
