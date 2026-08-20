"""The Kubernetes runtime against a real API server.

The unit tests assert what this system asks for. These assert that a cluster
accepts it and stores it unchanged -- which is a different question, and the
one that catches a manifest that renders but does not apply.

What is deliberately not covered here: whether a generator container starts and
produces load. That needs the generator image on the node, and it is what the
Docker integration suite already exercises end to end. These cover the launcher.

Skipped when no cluster is reachable, so `make test-int` stays a single command.
"""

from __future__ import annotations

import uuid

import pytest

from plimsoll_worker.runtime.base import GeneratorSpec
from plimsoll_worker.runtime.kubernetes import KubernetesRuntime, pod_name

pytestmark = pytest.mark.integration

NAMESPACE = "plimsoll-generators"


async def _runtime() -> KubernetesRuntime:
    from kubernetes_asyncio import config

    try:
        await config.load_kube_config()
    except Exception as exc:
        pytest.skip(f"no Kubernetes cluster is reachable: {exc}")
    return KubernetesRuntime(namespace=NAMESPACE)


def _spec(run_id: uuid.UUID, ordinal: int) -> GeneratorSpec:
    return GeneratorSpec(
        run_id=run_id,
        ordinal=ordinal,
        # Never pulled: these assertions are about the object the API server
        # stores, and a pod that stays Pending answers all of them.
        image="registry.k8s.io/pause:3.9",
        network="",
        environment={"PLIMSOLL_RUN_ID": str(run_id)},
        memory_limit="512m",
        cpu_limit=0.5,
    )


async def test_the_cluster_stores_a_generator_that_never_restarts() -> None:
    """Invariant 6, read back from the API server rather than from the dict
    this process sent. Defaulting and admission both happen in between."""
    runtime = await _runtime()
    run_id = uuid.uuid4()
    try:
        handles = await runtime.provision([_spec(run_id, 0)])
        assert [handle.external_ref for handle in handles] == [pod_name(run_id, 0)]

        api = await runtime._core()
        pod = await api.read_namespaced_pod(pod_name(run_id, 0), NAMESPACE)
        assert pod.spec.restart_policy == "Never"

        container = pod.spec.containers[0]
        assert container.resources.limits["memory"] == "512Mi"
        assert container.resources.limits["cpu"] == "500m"
        # Requests equal limits, so the scheduler places a generator on a node
        # that can actually give it what it was sized for.
        assert container.resources.requests == container.resources.limits
        assert container.security_context.allow_privilege_escalation is False
        assert container.security_context.run_as_non_root is True
    finally:
        await runtime.teardown(await runtime.find_by_run(run_id))


async def test_a_run_finds_its_own_pods_in_a_real_namespace() -> None:
    """Recovery reads the cluster rather than the database, so the label
    selector has to be right against a real one."""
    runtime = await _runtime()
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    try:
        await runtime.provision([_spec(mine, 0), _spec(mine, 1)])
        await runtime.provision([_spec(theirs, 0)])

        found = await runtime.find_by_run(mine)
        assert [handle.ordinal for handle in found] == [0, 1]
        assert all(str(mine) in handle.external_ref for handle in found)
    finally:
        await runtime.teardown(await runtime.find_by_run(mine))
        await runtime.teardown(await runtime.find_by_run(theirs))


async def test_a_repeated_provision_collides_rather_than_doubling_the_load() -> None:
    """The reason the name is derived from the run and the ordinal. A worker
    that died after creating a pod and before recording it must not be able to
    create a second one driving the same load."""
    from kubernetes_asyncio.client.exceptions import ApiException

    runtime = await _runtime()
    run_id = uuid.uuid4()
    try:
        await runtime.provision([_spec(run_id, 0)])
        with pytest.raises(ApiException) as clash:
            await runtime.provision([_spec(run_id, 0)])
        assert clash.value.status == 409
    finally:
        await runtime.teardown(await runtime.find_by_run(run_id))


async def test_teardown_removes_the_pod_and_is_idempotent() -> None:
    runtime = await _runtime()
    run_id = uuid.uuid4()
    handles = await runtime.provision([_spec(run_id, 0)])

    await runtime.teardown(handles)
    assert await runtime.find_by_run(run_id) == []
    # Already gone is the outcome teardown wanted.
    await runtime.teardown(handles)


async def test_status_reports_a_pod_that_no_longer_exists_as_absent() -> None:
    runtime = await _runtime()
    run_id = uuid.uuid4()
    handles = await runtime.provision([_spec(run_id, 0)])
    await runtime.teardown(handles)

    states = await runtime.status(handles)
    assert [state.present for state in states] == [False]


async def _may(subject: str, verb: str, resource: str, namespace: str) -> bool:
    """What the cluster says this service account may do, asked of the cluster
    rather than read off the Role."""
    from kubernetes_asyncio import client

    review = client.V1SubjectAccessReview(
        spec=client.V1SubjectAccessReviewSpec(
            user=subject,
            resource_attributes=client.V1ResourceAttributes(
                namespace=namespace, verb=verb, resource=resource
            ),
        )
    )
    runtime = await _runtime()
    api = await runtime._core()
    answer = await client.AuthorizationV1Api(api.api_client).create_subject_access_review(review)
    return bool(answer.status.allowed)


@pytest.mark.parametrize(
    ("verb", "resource", "namespace", "allowed"),
    [
        ("create", "pods", NAMESPACE, True),
        ("delete", "pods", NAMESPACE, True),
        ("list", "pods", NAMESPACE, True),
        # The worker launches load generators. Everything below is something it
        # has no reason to do, and the Role rather than a ClusterRole is what
        # keeps a mistake here out of the rest of the cluster.
        ("get", "secrets", NAMESPACE, False),
        ("get", "secrets", "plimsoll", False),
        ("create", "pods", "plimsoll", False),
        ("create", "pods", "kube-system", False),
        ("delete", "nodes", "", False),
    ],
)
async def test_the_worker_may_launch_generators_and_nothing_else(
    verb: str, resource: str, namespace: str, allowed: bool
) -> None:
    """The chart's RBAC, checked against a cluster that has it installed."""
    subject = "system:serviceaccount:plimsoll:plim-worker"
    runtime = await _runtime()
    api = await runtime._core()
    from kubernetes_asyncio.client.exceptions import ApiException

    try:
        await client_rbac_present(api)
    except ApiException:
        pytest.skip("the chart is not installed in this cluster")

    assert await _may(subject, verb, resource, namespace) is allowed


async def client_rbac_present(api: object) -> None:
    """Raises if the chart's Role is absent, so the checks above skip rather
    than report a permission boundary that nothing has actually configured."""
    from kubernetes_asyncio import client

    await client.RbacAuthorizationV1Api(api.api_client).read_namespaced_role(  # type: ignore[attr-defined]
        "plim-generators", NAMESPACE
    )
