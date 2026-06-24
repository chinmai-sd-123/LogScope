"""Agent <-> server integration tests over a real loopback socket.

Covers the happy path, idempotent de-duplication of resent batches, and the
headline resilience story: kill the server mid-stream, the agent buffers and
reconnects, and no events are lost.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from logscope.index.store import EventStore
from logscope.model import Level, LogEvent
from logscope.net.agent import Agent
from logscope.net.server import Server


def _ev(message):
    return LogEvent(
        timestamp=datetime.now(timezone.utc), level=Level.INFO,
        source="api", message=message, raw=message,
    )


class ListSource:
    """Emits a fixed list of events then idles until stopped."""

    name = "list"

    def __init__(self, events):
        self._events = events

    async def events(self, stop=None):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)
        while stop is not None and not stop.is_set():
            await asyncio.sleep(0.01)


async def _wait_until(predicate, timeout=3.0, interval=0.02):
    elapsed = 0.0
    while elapsed < timeout:
        if predicate():
            return True
        await asyncio.sleep(interval)
        elapsed += interval
    return False


@pytest.mark.asyncio
async def test_agent_ships_events_to_server():
    store = EventStore(":memory:")
    server = Server(store, host="127.0.0.1", port=0)
    port = await server.start()

    events = [_ev(f"event {i}") for i in range(5)]
    agent = Agent("agent-1", [ListSource(events)], "127.0.0.1", port,
                  batch_size=2, flush_interval=0.05)
    task = asyncio.create_task(agent.run())

    assert await _wait_until(lambda: store.count() == 5)
    agent.stop()
    task.cancel()
    await server.stop()
    store.close()


@pytest.mark.asyncio
async def test_idempotent_redelivery():
    store = EventStore(":memory:")
    server = Server(store, host="127.0.0.1", port=0)
    port = await server.start()

    # Same event enqueued twice -> same stable event_id -> stored once.
    dup = _ev("duplicate event")
    agent = Agent("agent-1", [ListSource([dup, dup])], "127.0.0.1", port,
                  batch_size=10, flush_interval=0.05)
    task = asyncio.create_task(agent.run())

    assert await _wait_until(lambda: store.count() >= 1)
    await asyncio.sleep(0.2)
    assert store.count() == 1  # de-duplicated

    agent.stop()
    task.cancel()
    await server.stop()
    store.close()


@pytest.mark.asyncio
async def test_bad_frame_is_acked_not_hung():
    # A malformed/old-version frame must be dropped AND acked, or the agent
    # blocks forever waiting on an ack. Send a bad frame then a good one over a
    # raw connection and check both get acked and the good event lands.
    import struct

    from logscope.net.protocol import encode_batch

    store = EventStore(":memory:")
    server = Server(store, host="127.0.0.1", port=0)
    port = await server.start()

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    bad = b'{"version": 999, "events": []}'  # wrong version
    good = encode_batch("agent-x", [_ev("real event")])
    for payload in (bad, good):
        writer.write(struct.pack(">I", len(payload)) + payload)
    await writer.drain()

    ack1 = await asyncio.wait_for(reader.readexactly(1), timeout=2.0)
    ack2 = await asyncio.wait_for(reader.readexactly(1), timeout=2.0)
    assert ack1 == b"\x06" and ack2 == b"\x06"
    assert store.count() == 1  # only the good batch stored

    writer.close()
    await server.stop()
    store.close()


@pytest.mark.asyncio
async def test_no_loss_when_server_dies_and_recovers():
    store = EventStore(":memory:")
    server = Server(store, host="127.0.0.1", port=0)
    port = await server.start()

    events = [_ev(f"msg {i}") for i in range(10)]
    agent = Agent("agent-1", [ListSource(events)], "127.0.0.1", port,
                  batch_size=3, flush_interval=0.05, backoff_cap=0.2)
    task = asyncio.create_task(agent.run())

    # Let a few batches land, then kill the server mid-stream.
    assert await _wait_until(lambda: store.count() >= 3)
    await server.stop()
    await asyncio.sleep(0.2)  # agent now buffering + retrying to a dead server

    # Bring a fresh server up on the same port, same store.
    server2 = Server(store, host="127.0.0.1", port=port)
    await server2.start()

    # All 10 events eventually arrive despite the outage.
    assert await _wait_until(lambda: store.count() == 10, timeout=5.0)

    agent.stop()
    task.cancel()
    await server2.stop()
    store.close()
