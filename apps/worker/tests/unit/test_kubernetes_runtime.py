"""What the manifest says, and what the runtime does with what comes back.

A fake API server rather than a real cluster: these are assertions about the
shape of the pod this system asks for, and about how it reads the answers.
Whether a cluster accepts that shape is a different question, answered by
applying the manifest to a real API server.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from plimsoll_worker.runtime.base import GeneratorHandle, GeneratorSpec
from plimsoll_worker.runtime.kubernetes import KubernetesRuntime, _memory, pod_name

RUN = uuid.UUID("11111111-2222-3333-4444-555555555555")


class FakeApi:
    def __init__(self, pods: dict[str, Any] | None = None) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, int | None]] = []
        self.pods = pods or {}
        self.refuse_after: int | None = None

    async def create_namespaced_pod(self, namespace: str, body: dict[str, Any]) -> None:
        if self.refuse_after is not None and len(self.created) >= self.refuse_after:
            raise ApiException(status=409, reason="AlreadyExists")
        self.created.append(body)

    async def list_namespaced_pod(self, namespace: str, label_selector: str = "") -> Any:
        wanted = label_selector.split("=", 1)[1] if "=" in label_selector else None
        items = [
            pod
            for pod in self.pods.values()
            if wanted is None or (pod.metadata.labels or {}).get("plimsoll.run") == wanted
        ]
        return SimpleNamespace(items=items)

    async def read_namespaced_pod(self, name: str, namespace: str) -> Any:
        if name not in self.pods:
            raise ApiException(status=404, reason="NotFound")
        return self.pods[name]

    async def delete_namespaced_pod(
        self, name: str, namespace: str, grace_period_seconds: int | None = None
    ) -> None:
        if name not in self.pods:
            raise ApiException(status=404, reason="NotFound")
        self.deleted.append((name, grace_period_seconds))
        del self.pods[name]


def _pod(ordinal: int, phase: str, exit_code: int | None = None) -> Any:
    terminated = SimpleNamespace(exit_code=exit_code) if exit_code is not None else None
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=pod_name(RUN, ordinal),
            labels={"plimsoll.run": str(RUN), "plimsoll.ordinal": str(ordinal)},
        ),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=[SimpleNamespace(state=SimpleNamespace(terminated=terminated))],
        ),
    )


def _spec(ordinal: int, **overrides: Any) -> GeneratorSpec:
    body: dict[str, Any] = {
        "run_id": RUN,
        "ordinal": ordinal,
        "image": "ghcr.io/ultron13/plimsoll-generator:0.1.0",
        "network": "",
        "environment": {"PLIMSOLL_RUN_ID": str(RUN)},
    }
    body.update(overrides)
    return GeneratorSpec(**body)


async def test_a_generator_never_restarts() -> None:
    """Invariant 6. A Job would retry and a Deployment would reschedule; the
    pod says Never, which is why it is a bare pod."""
    api = FakeApi()
    await KubernetesRuntime(api=api).provision([_spec(0)])
    assert api.created[0]["kind"] == "Pod"
    assert api.created[0]["spec"]["restartPolicy"] == "Never"


async def test_a_generator_is_bounded_and_requests_what_it_limits() -> None:
    """Under-requesting lets the scheduler pack generators onto a node that
    then throttles them, and a throttled load generator measures itself."""
    api = FakeApi()
    await KubernetesRuntime(api=api).provision([_spec(0, memory_limit="4g", cpu_limit=3.0)])
    resources = api.created[0]["spec"]["containers"][0]["resources"]
    assert resources["limits"] == {"memory": "4Gi", "cpu": "3.0"}
    assert resources["requests"] == resources["limits"]


async def test_a_pool_that_says_nothing_still_gets_a_bound() -> None:
    api = FakeApi()
    await KubernetesRuntime(api=api).provision([_spec(0)])
    limits = api.created[0]["spec"]["containers"][0]["resources"]["limits"]
    assert limits["memory"] == "2Gi" and limits["cpu"] == "2.0"


@pytest.mark.parametrize(
    ("given", "expected"),
    [("2g", "2Gi"), ("512m", "512Mi"), ("4Gi", "4Gi"), ("1024Mi", "1024Mi"), (None, "2Gi")],
)
def test_docker_memory_sizes_are_understood(given: str | None, expected: str) -> None:
    """Pools are configured once and used by both runtimes, so `2g` has to
    mean the same thing in each."""
    assert _memory(given) == expected


async def test_provisioning_is_all_or_nothing() -> None:
    """A pod created without a database row is one nothing will ever reap."""
    api = FakeApi()
    api.refuse_after = 2
    runtime = KubernetesRuntime(api=api)
    api.pods = {pod_name(RUN, 0): _pod(0, "Pending"), pod_name(RUN, 1): _pod(1, "Pending")}

    with pytest.raises(ApiException):
        await runtime.provision([_spec(0), _spec(1), _spec(2)])

    assert [name for name, _ in api.deleted] == [pod_name(RUN, 0), pod_name(RUN, 1)]
    assert api.pods == {}


async def test_a_name_is_derived_so_a_repeat_collides_rather_than_doubling() -> None:
    """A worker that died after creating a pod and before recording it must
    not be able to create a second one driving the same load."""
    assert pod_name(RUN, 3) == f"plimsoll-gen-{RUN}-3"
    assert len(pod_name(RUN, 3)) <= 63


async def test_a_run_finds_its_own_pods_and_no_others() -> None:
    other = uuid.uuid4()
    api = FakeApi(
        {
            pod_name(RUN, 1): _pod(1, "Running"),
            pod_name(RUN, 0): _pod(0, "Running"),
            "plimsoll-gen-other-0": SimpleNamespace(
                metadata=SimpleNamespace(
                    name="plimsoll-gen-other-0",
                    labels={"plimsoll.run": str(other), "plimsoll.ordinal": "0"},
                ),
                status=SimpleNamespace(phase="Running", container_statuses=[]),
            ),
        }
    )
    found = await KubernetesRuntime(api=api).find_by_run(RUN)
    assert [handle.ordinal for handle in found] == [0, 1]


async def test_a_finished_pod_is_capacity_loss_not_a_live_generator() -> None:
    """`Succeeded` and `Failed` are terminal. A run watching for capacity loss
    has to see that rather than a pod object that still answers."""
    api = FakeApi(
        {
            pod_name(RUN, 0): _pod(0, "Running"),
            pod_name(RUN, 1): _pod(1, "Failed", exit_code=137),
            pod_name(RUN, 2): _pod(2, "Succeeded", exit_code=0),
        }
    )
    states = await KubernetesRuntime(api=api).status(
        [GeneratorHandle(ordinal=i, external_ref=pod_name(RUN, i)) for i in range(3)]
    )
    assert [state.present for state in states] == [True, False, False]
    assert [state.exit_code for state in states] == [None, 137, 0]


async def test_a_pod_that_is_gone_reports_absent_rather_than_raising() -> None:
    states = await KubernetesRuntime(api=FakeApi()).status(
        [GeneratorHandle(ordinal=0, external_ref=pod_name(RUN, 0))]
    )
    assert states == [
        type(states[0])(ordinal=0, present=False, exit_code=None),
    ]


async def test_teardown_gives_the_agent_time_to_upload() -> None:
    """A zero grace period truncates the results the run exists to produce."""
    api = FakeApi({pod_name(RUN, 0): _pod(0, "Running")})
    await KubernetesRuntime(api=api).teardown(
        [GeneratorHandle(ordinal=0, external_ref=pod_name(RUN, 0))]
    )
    assert api.deleted == [(pod_name(RUN, 0), 30)]


async def test_tearing_down_something_already_gone_is_the_outcome_wanted() -> None:
    api = FakeApi()
    await KubernetesRuntime(api=api).teardown(
        [GeneratorHandle(ordinal=0, external_ref=pod_name(RUN, 0))]
    )
    assert api.deleted == []
