"""Durable queueing and live announcements, both on Redis.

ADR-0005 chose Redis Streams for v0.1 and put a seam in front of it so RabbitMQ
or Kafka is a configuration change rather than a refactor. Everything the
platform enqueues goes through `MessageBus`; nothing calls redis directly.

Delivery is at-least-once. That is not a caveat to work around -- it is the
contract consumers are written against.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol, cast

import redis.asyncio as redis
from redis.exceptions import ResponseError

from plimsoll_api.config import get_settings

RUNS_EXECUTION = "runs.execution"
# Diagnostics, asked of the worker because it is the only process holding a
# container runtime. Kept off the execution stream so a probe can never sit
# behind a run, nor a run behind a probe.
POOL_PROBES = "pools.probe"
WORKER_GROUP = "orchestrators"


def run_channel(run_id: Any) -> str:
    """Commands for one run's agents. Namespaced so a listener cannot subscribe
    to another run by guessing a shorter key."""
    return f"runs:{run_id}:commands"


def probe_channel(probe_id: Any) -> str:
    """Where the answer to one probe is published, and nowhere else."""
    return f"pools:probe:{probe_id}"


@dataclass(frozen=True)
class Delivery:
    id: str
    stream: str
    payload: dict[str, str]


class MessageBus(Protocol):
    async def publish(self, stream: str, payload: dict[str, str]) -> str: ...

    async def ensure_group(self, stream: str, group: str) -> None: ...

    async def read(
        self, stream: str, group: str, consumer: str, *, count: int, block_ms: int
    ) -> list[Delivery]: ...

    async def acknowledge(self, stream: str, group: str, delivery: Delivery) -> None: ...

    async def reclaim_stale(
        self, stream: str, group: str, consumer: str, *, idle: timedelta
    ) -> list[Delivery]: ...

    async def announce(self, channel: str, payload: dict[str, str]) -> None: ...


class RedisStreamBus:
    def __init__(self, url: str | None = None) -> None:
        self._client = redis.from_url(url or get_settings().redis_url, decode_responses=True)

    async def publish(self, stream: str, payload: dict[str, str]) -> str:
        # redis-py's stub is invariant in the field/value dict and, with
        # decode_responses=True, still types the id as `bytes | str`; both are
        # library-typing gaps, not ambiguity about what xadd actually returns.
        message_id = await self._client.xadd(stream, cast(dict[Any, Any], payload))
        return cast(str, message_id)

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            # mkstream so a consumer may start before the first producer.
            await self._client.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read(
        self, stream: str, group: str, consumer: str, *, count: int, block_ms: int
    ) -> list[Delivery]:
        response = await self._client.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms
        )
        # XREADGROUP's real shape with decode_responses=True: a list of
        # (stream name, [(message id, field dict), ...]) pairs. The stub types
        # this as a loose Any-laden union, so narrow it to what redis sends.
        entries = cast(list[tuple[str, list[tuple[str, dict[str, str]]]]], response or [])
        return [
            Delivery(id=message_id, stream=stream_name, payload=payload)
            for stream_name, messages in entries
            for message_id, payload in messages
        ]

    async def acknowledge(self, stream: str, group: str, delivery: Delivery) -> None:
        await self._client.xack(stream, group, delivery.id)

    async def reclaim_stale(
        self, stream: str, group: str, consumer: str, *, idle: timedelta
    ) -> list[Delivery]:
        """Take over messages a dead consumer is still holding."""
        _, messages, _ = await self._client.xautoclaim(
            stream, group, consumer, int(idle.total_seconds() * 1000), start_id="0"
        )
        return [
            Delivery(id=message_id, stream=stream, payload=payload)
            for message_id, payload in messages
        ]

    async def announce(self, channel: str, payload: dict[str, str]) -> None:
        await self._client.publish(channel, json.dumps(payload))

    @asynccontextmanager
    async def listen(self, channel: str) -> AsyncIterator[AsyncIterator[dict[str, str]]]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)

        async def messages() -> AsyncIterator[dict[str, str]]:
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    decoded: dict[str, str] = json.loads(raw["data"])
                    yield decoded

        try:
            yield messages()
        finally:
            await pubsub.unsubscribe(channel)
            # PubSub.aclose has no stub signature in redis-py.
            await pubsub.aclose()  # type: ignore[no-untyped-call]


_bus: RedisStreamBus | None = None


def get_bus() -> RedisStreamBus:
    """One client per process; redis-py pools its connections internally."""
    global _bus
    if _bus is None:
        _bus = RedisStreamBus()
    return _bus
