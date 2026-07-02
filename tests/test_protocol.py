import asyncio
import struct
from datetime import datetime, timezone

import pytest

from logscope.model import Level, LogEvent
from logscope.net.protocol import (
    PROTOCOL_VERSION,
    decode_batch,
    encode_batch,
    event_from_dict,
    event_to_dict,
    read_frame,
)


def _ev(message="hello", level=Level.INFO):
    return LogEvent(
        timestamp=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        level=level, source="api", message=message, raw=message,
        fields={"request_id": "a1"},
    )


# --------------------------------------------------------------------------- #
# Event + batch round trips
# --------------------------------------------------------------------------- #


def test_event_dict_round_trip():
    ev = _ev("db down", Level.ERROR)
    back = event_from_dict(event_to_dict(ev))
    assert back.message == ev.message
    assert back.level == ev.level
    assert back.timestamp == ev.timestamp
    assert back.fields == ev.fields


def test_batch_round_trip():
    events = [_ev("a"), _ev("b"), _ev("c")]
    agent_id, decoded = decode_batch(encode_batch("agent-1", events))
    assert agent_id == "agent-1"
    assert [e.message for e in decoded] == ["a", "b", "c"]


def test_version_mismatch_rejected(monkeypatch):
    import logscope.net.protocol as proto

    payload = encode_batch("agent-1", [_ev()])
    monkeypatch.setattr(proto, "PROTOCOL_VERSION", PROTOCOL_VERSION + 1)
    with pytest.raises(ValueError):
        decode_batch(payload)


# --------------------------------------------------------------------------- #
# Framing, including split reads (the classic TCP boundary problem)
# --------------------------------------------------------------------------- #


class _FakeReader:
    """Feeds bytes in deliberately awkward chunks to exercise readexactly."""

    def __init__(self, data: bytes, chunk: int = 3):
        self._data = data
        self._chunk = chunk
        self._pos = 0

    async def readexactly(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            if self._pos >= len(self._data):
                raise asyncio.IncompleteReadError(out, n)
            take = min(self._chunk, n - len(out), len(self._data) - self._pos)
            out += self._data[self._pos : self._pos + take]
            self._pos += take
        return out


@pytest.mark.asyncio
async def test_read_frame_handles_split_reads():
    payload = encode_batch("agent-1", [_ev("split me")])
    framed = struct.pack(">I", len(payload)) + payload
    reader = _FakeReader(framed, chunk=3)  # 3 bytes at a time
    got = await read_frame(reader)
    assert got == payload
    agent_id, events = decode_batch(got)
    assert events[0].message == "split me"


@pytest.mark.asyncio
async def test_read_frame_rejects_oversize_length():
    import logscope.net.protocol as proto

    framed = struct.pack(">I", proto.MAX_FRAME_BYTES + 1)
    reader = _FakeReader(framed, chunk=4)
    with pytest.raises(ValueError):
        await read_frame(reader)
