"""Generators as bare pods, one per ordinal.

A pod rather than a Job or a Deployment, and that is the whole design. Both of
the others exist to make work survive the loss of a node, and invariant 6 says
a generator must not: a Job retries under `backoffLimit`, a Deployment
reschedules, and either would restart a generator mid-run. A restarted
generator rejoins with an empty histogram and a fresh ramp-up, so the run it
rejoins reports numbers that describe neither the old load nor the new. Losing
capacity is a result that can be reported; quietly changing the experiment is
not. `restartPolicy: Never` on a pod nothing owns is the only shape that fails
the way this system needs it to.

The pod name is the handle, and it is derived from the run and the ordinal
rather than generated. That makes provisioning idempotent against a worker
that died between creating a pod and recording it: the second attempt collides
by name instead of quietly doubling the load.
"""

from __future__ import annotations

import uuid
from typing import Any

from kubernetes_asyncio import client, config
from kubernetes_asyncio.client.exceptions import ApiException

from plimsoll_worker.runtime.base import GeneratorHandle, GeneratorSpec, GeneratorState

DEFAULT_MEMORY_LIMIT = "2Gi"
DEFAULT_CPU_LIMIT = 2.0

# Phases that mean the pod still exists as capacity. `Succeeded` and `Failed`
# are terminal: the generator is gone, and a run watching for capacity loss
# needs to see that rather than a pod object that still answers.
LIVE_PHASES = frozenset({"Pending", "Running", "Unknown"})

RUN_LABEL = "plimsoll.run"
ORDINAL_LABEL = "plimsoll.ordinal"


def pod_name(run_id: uuid.UUID, ordinal: int) -> str:
    """Deterministic, and a valid DNS-1123 label.

    A UUID is 36 characters and the limit is 63, so the ordinal fits with room
    to spare. Deriving it rather than generating one is what makes a repeated
    provision collide instead of doubling the load.
    """
    return f"plimsoll-gen-{run_id}-{ordinal}"


def _memory(limit: str | None) -> str:
    """Docker's `2g` and Kubernetes' `2Gi` mean the same thing and are spelled
    differently. Pools are configured once, so the translation lives here
    rather than in every pool."""
    value = (limit or DEFAULT_MEMORY_LIMIT).strip()
    if value.lower().endswith(("gi", "mi", "ki")):
        return value[:-2] + value[-2:].capitalize()
    if value.lower().endswith(("g", "m", "k")):
        return f"{value[:-1]}{value[-1].upper()}i"
    return value


class KubernetesRuntime:
    """The production runtime. Docker is the development one."""

    def __init__(self, namespace: str = "plimsoll", *, api: Any | None = None) -> None:
        self._namespace = namespace
        self._api = api
        self._loaded = api is not None

    async def _core(self) -> Any:
        if not self._loaded:
            try:
                # In-cluster first: the worker runs as a pod in production and
                # its service account is the credential. A kubeconfig is the
                # development and operator case.
                config.load_incluster_config()  # type: ignore[no-untyped-call]
            except config.ConfigException:
                await config.load_kube_config()
            self._loaded = True
        if self._api is None:
            self._api = client.CoreV1Api()
        return self._api

    def _manifest(self, spec: GeneratorSpec) -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": pod_name(spec.run_id, spec.ordinal),
                "namespace": self._namespace,
                "labels": {
                    **spec.labels,
                    RUN_LABEL: str(spec.run_id),
                    ORDINAL_LABEL: str(spec.ordinal),
                },
            },
            "spec": {
                # Invariant 6, stated where Kubernetes reads it.
                "restartPolicy": "Never",
                # A generator that outlives its run is capacity nobody is
                # using and load nobody asked for. This is the backstop for a
                # worker that never gets to tear it down; the reconciler is
                # the normal path.
                "activeDeadlineSeconds": 24 * 60 * 60,
                "containers": [
                    {
                        "name": "generator",
                        "image": spec.image,
                        "env": [
                            {"name": key, "value": value}
                            for key, value in sorted(spec.environment.items())
                        ],
                        # Requests equal limits, so a generator is scheduled on
                        # a node that can actually give it what it was sized
                        # for. Under-requesting would let the scheduler pack
                        # generators onto a node that then throttles them, and
                        # a throttled load generator measures itself.
                        "resources": {
                            "requests": {
                                "memory": _memory(spec.memory_limit),
                                "cpu": str(spec.cpu_limit or DEFAULT_CPU_LIMIT),
                            },
                            "limits": {
                                "memory": _memory(spec.memory_limit),
                                "cpu": str(spec.cpu_limit or DEFAULT_CPU_LIMIT),
                            },
                        },
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": False,
                            "runAsNonRoot": True,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
            },
        }

    async def provision(self, specs: list[GeneratorSpec]) -> list[GeneratorHandle]:
        """All of them or none of them.

        A half-provisioned run is worse than a failed one: the pods that were
        created carry no database row yet, so nothing downstream knows to reap
        them and they outlive the run that wanted them.
        """
        api = await self._core()
        handles: list[GeneratorHandle] = []
        try:
            for spec in specs:
                await api.create_namespaced_pod(self._namespace, self._manifest(spec))
                handles.append(
                    GeneratorHandle(
                        ordinal=spec.ordinal,
                        external_ref=pod_name(spec.run_id, spec.ordinal),
                    )
                )
        except Exception:
            await self.teardown(handles)
            raise
        return handles

    async def find_by_run(self, run_id: uuid.UUID) -> list[GeneratorHandle]:
        """What this run actually has, asked of the cluster rather than the
        database. The two disagree exactly when it matters."""
        api = await self._core()
        listing = await api.list_namespaced_pod(
            self._namespace, label_selector=f"{RUN_LABEL}={run_id}"
        )
        found = []
        for pod in listing.items:
            ordinal = (pod.metadata.labels or {}).get(ORDINAL_LABEL)
            if ordinal is None:
                continue
            found.append(GeneratorHandle(ordinal=int(ordinal), external_ref=pod.metadata.name))
        return sorted(found, key=lambda handle: handle.ordinal)

    async def status(self, handles: list[GeneratorHandle]) -> list[GeneratorState]:
        api = await self._core()
        states = []
        for handle in handles:
            try:
                pod = await api.read_namespaced_pod(handle.external_ref, self._namespace)
            except ApiException as exc:
                if exc.status != 404:
                    raise
                states.append(GeneratorState(ordinal=handle.ordinal, present=False))
                continue
            states.append(
                GeneratorState(
                    ordinal=handle.ordinal,
                    present=pod.status.phase in LIVE_PHASES,
                    exit_code=_exit_code(pod),
                )
            )
        return states

    async def teardown(self, handles: list[GeneratorHandle]) -> None:
        api = await self._core()
        for handle in handles:
            try:
                await api.delete_namespaced_pod(
                    handle.external_ref,
                    self._namespace,
                    # The agent is asked to finish and upload what it has. A
                    # zero grace period would truncate the results the run
                    # exists to produce.
                    grace_period_seconds=30,
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise
                # Already gone is the outcome teardown wanted.

    async def check(self, image: str) -> tuple[bool, str]:
        """Can this runtime create a generator right now?

        The image is deliberately not verified. Kubernetes pulls on the node
        that ends up scheduling the pod, so nothing the control plane can ask
        would be true of every node -- and answering from the worker's own view
        would be a check that passes and then fails at pull time. What is worth
        checking is what this system actually needs: an API server that answers
        and permission to create a pod in the namespace.
        """
        try:
            api = await self._core()
        except Exception as exc:
            return False, f"The cluster is unreachable: {exc}"

        review = client.V1SelfSubjectAccessReview(
            spec=client.V1SelfSubjectAccessReviewSpec(
                resource_attributes=client.V1ResourceAttributes(
                    namespace=self._namespace, verb="create", resource="pods"
                )
            )
        )
        try:
            answer = await client.AuthorizationV1Api(
                api.api_client
            ).create_self_subject_access_review(review)
        except ApiException as exc:
            return False, f"The cluster refused the access review: {exc.reason}."
        if not answer.status.allowed:
            return False, (
                f"This service account may not create pods in {self._namespace}. "
                f"{answer.status.reason or ''}".strip()
            )
        return True, (
            f"The cluster is reachable and pods may be created in {self._namespace}. "
            f"The image {image} is pulled by the node that schedules the generator."
        )


def _exit_code(pod: Any) -> int | None:
    for status in pod.status.container_statuses or []:
        terminated = getattr(status.state, "terminated", None)
        if terminated is not None:
            return int(terminated.exit_code)
    return None
