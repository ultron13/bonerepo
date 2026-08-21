"""Redis Streams, exercised against the real broker.

At-least-once delivery is the property the worker is built on, so the test that
matters most here is that an unacknowledged message comes back.
"""

import uuid
from datetime import timedelta

import pytest

from plimsoll_api.messaging import RedisStreamBus

pytestmark = pytest.mark.integration

GROUP = "test-group"


@pytest.fixture
def stream() -> str:
    return f"test.stream.{uuid.uuid4().hex[:8]}"


async def test_a_published_message_is_read_by_the_group(stream: str) -> None:
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.publish(stream, {"runId": "abc"})

    deliveries = await bus.read(stream, GROUP, "consumer-1", count=10, block_ms=1000)
    assert [delivery.payload["runId"] for delivery in deliveries] == ["abc"]


async def test_an_acknowledged_message_is_not_reclaimed(stream: str) -> None:
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.publish(stream, {"runId": "abc"})

    delivered = await bus.read(stream, GROUP, "consumer-1", count=10, block_ms=1000)
    await bus.acknowledge(stream, GROUP, delivered[0])

    reclaimed = await bus.reclaim_stale(stream, GROUP, "consumer-2", idle=timedelta(seconds=0))
    assert reclaimed == []


async def test_an_unacknowledged_message_is_reclaimed(stream: str) -> None:
    """A worker that dies holding a message must not take the run with it."""
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.publish(stream, {"runId": "abc"})
    await bus.read(stream, GROUP, "dying-consumer", count=10, block_ms=1000)

    reclaimed = await bus.reclaim_stale(
        stream, GROUP, "surviving-consumer", idle=timedelta(seconds=0)
    )
    assert [delivery.payload["runId"] for delivery in reclaimed] == ["abc"]


async def test_reading_an_empty_stream_returns_nothing(stream: str) -> None:
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    assert await bus.read(stream, GROUP, "consumer-1", count=10, block_ms=50) == []


async def test_creating_a_group_twice_is_not_an_error(stream: str) -> None:
    """Every worker calls this at startup; only one of them is first."""
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.ensure_group(stream, GROUP)


async def test_an_announcement_reaches_a_listener() -> None:
    channel = f"test.channel.{uuid.uuid4().hex[:8]}"
    bus = RedisStreamBus()
    received: list[dict[str, str]] = []

    async with bus.listen(channel) as messages:
        await bus.announce(channel, {"command": "stop"})
        async for message in messages:
            received.append(message)
            break

    assert received == [{"command": "stop"}]


async def test_a_stream_does_not_grow_without_bound(
    stream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acknowledging does not remove an entry.

    Redis keeps what it was given until something trims it, so a stream that
    nothing caps grows in one direction only, and the end of that is an
    eviction or an out-of-memory that takes the control plane with it. Found
    at ninety-seven thousand entries on a development machine.

    A small cap is set for this stream so the trimming actually happens.
    Asserting against the real cap would pass whether or not a cap existed,
    which is the shape of test that reports success for years and means
    nothing -- ten thousand entries is not something to write in a test, and
    not writing them is not evidence of anything either.
    """
    from plimsoll_api import messaging

    monkeypatch.setitem(messaging.STREAM_CAPS, stream, 20)
    bus = messaging.get_bus()
    for index in range(400):
        await bus.publish(stream, {"n": str(index)})

    length = await bus.client.xlen(stream)
    assert length > 0, "the premise: entries were written"
    # Approximate trimming keeps at least the cap and lets the stream run over
    # it between radix nodes, so this is bounded rather than exact. Four
    # hundred entries against a cap of twenty is far outside that slack.
    assert length < 400, f"nothing was trimmed: {length} entries survived 400 writes"
