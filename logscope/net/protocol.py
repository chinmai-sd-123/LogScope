"""The wire protocol: length-prefixed JSON frames carrying event batches.

A raw TCP stream has no message boundaries, so we frame every payload with a
4-byte big-endian length prefix, then read exactly that many bytes back. This is
the fundamental fix for the "where does one message end" problem.

Payload: a JSON object ``{version, agent_id, events: [...]}``. JSON is chosen for
debuggability (you can tcpdump and read it) at the cost of some size; every frame
carries a schema ``version`` so a server can reject or adapt to mismatched agents
(forward/backward compatibility).
"""

from __future__ import annotations

import json
import struct
from datetime import datetime, timezone
from typing import List

from logscope.model import Level, LogEvent

PROTOCOL_VERSION = 1
_HEADER = struct.Struct(">I")  # 4-byte big-endian unsigned length
MAX_FRAME_BYTES = 64 * 1024 * 1024  # guard against a bogus/huge length prefix


# --------------------------------------------------------------------------- #
# Event (de)serialization
# --------------------------------------------------------------------------- #


def event_to_dict(event: LogEvent) -> dict:
    return {
        "ts": event.timestamp.isoformat(),
        "level": int(event.level),
        "source": event.source,
        "message": event.message,
        "raw": event.raw,
        "fields": event.fields or {},
    }


def event_from_dict(d: dict) -> LogEvent:
    ts = datetime.fromisoformat(d["ts"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return LogEvent(
        timestamp=ts,
        level=Level(int(d["level"])),
        source=d["source"],
        message=d["message"],
        raw=d["raw"],
        fields=d.get("fields") or {},
    )


# --------------------------------------------------------------------------- #
# Batch encode / decode
# --------------------------------------------------------------------------- #


def encode_batch(agent_id: str, events: List[LogEvent]) -> bytes:
    payload = {
        "version": PROTOCOL_VERSION,
        "agent_id": agent_id,
        "events": [event_to_dict(e) for e in events],
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def decode_batch(payload: bytes) -> tuple[str, List[LogEvent]]:
    """Decode a payload into ``(agent_id, events)``.

    Raises ``ValueError`` on a version mismatch or malformed payload so the
    caller can decide how to handle a bad/old agent.
    """
    obj = json.loads(payload.decode("utf-8"))
    version = obj.get("version")
    if version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported protocol version {version!r}")
    events = [event_from_dict(e) for e in obj.get("events", [])]
    return obj.get("agent_id", "unknown"), events


# --------------------------------------------------------------------------- #
# Framing over asyncio streams
# --------------------------------------------------------------------------- #


async def write_frame(writer, payload: bytes) -> None:
    """Write a length-prefixed frame, respecting socket backpressure via drain."""
    writer.write(_HEADER.pack(len(payload)))
    writer.write(payload)
    await writer.drain()


async def read_frame(reader) -> bytes:
    """Read exactly one length-prefixed frame.

    ``readexactly`` handles the case where a message is split across multiple TCP
    reads. Raises ``asyncio.IncompleteReadError`` on a clean EOF mid-frame.
    """
    header = await reader.readexactly(_HEADER.size)
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ValueError(f"frame length {length} exceeds limit {MAX_FRAME_BYTES}")
    return await reader.readexactly(length)
