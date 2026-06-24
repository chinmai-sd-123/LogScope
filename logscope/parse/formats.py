"""Format detectors.

Each detector takes a raw line and returns a ParsedLine if it recognizes the
format, or None to let the next one try. Detectors must not raise. The Parser
tries them in order and falls back to treating the line as unstructured.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# --------------------------------------------------------------------------- #
# Result type shared by every detector.
# --------------------------------------------------------------------------- #


@dataclass
class ParsedLine:
    """The raw, format-specific extraction before normalization.

    ``level`` and ``timestamp`` are left as strings here; the parser normalizes
    them. Keeping detection and normalization separate keeps each piece simple.
    """

    message: str
    level: Optional[str] = None
    timestamp: Optional[str] = None
    fields: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Detectors, tried in order of specificity.
# --------------------------------------------------------------------------- #

# Common keys used across structured formats to mean "the message" / "the time".
_MESSAGE_KEYS = ("message", "msg", "log", "text")
_TIME_KEYS = ("timestamp", "time", "ts", "@timestamp", "datetime")
_LEVEL_KEYS = ("level", "lvl", "severity", "loglevel")


def _pick(d: dict, keys: tuple[str, ...]) -> Optional[str]:
    """Return the first present key's value (as str), case-insensitively."""
    lowered = {k.lower(): v for k, v in d.items()}
    for key in keys:
        if key in lowered and lowered[key] is not None:
            return str(lowered[key])
    return None


def detect_json(line: str) -> Optional[ParsedLine]:
    """JSON-lines: each line is a JSON object."""
    s = line.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None

    message = _pick(obj, _MESSAGE_KEYS) or ""
    level = _pick(obj, _LEVEL_KEYS)
    timestamp = _pick(obj, _TIME_KEYS)

    # Everything that isn't a recognized special key becomes an extracted field.
    consumed = {k.lower() for k in (*_MESSAGE_KEYS, *_TIME_KEYS, *_LEVEL_KEYS)}
    fields = {k: v for k, v in obj.items() if k.lower() not in consumed}
    return ParsedLine(message=message, level=level, timestamp=timestamp, fields=fields)


# logfmt: key=value pairs, values optionally double-quoted. Common in Go services.
_LOGFMT_PAIR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def detect_logfmt(line: str) -> Optional[ParsedLine]:
    """logfmt: ``level=info msg="hello world" request_id=a1b2``."""
    pairs = _LOGFMT_PAIR.findall(line)
    if not pairs:
        return None
    # Require at least one recognized structural key to avoid misfiring on prose
    # that merely contains an "x=y" substring.
    obj: dict[str, str] = {}
    for key, value in pairs:
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"')
        obj[key] = value

    has_structure = any(
        k.lower() in (*_MESSAGE_KEYS, *_LEVEL_KEYS, *_TIME_KEYS) for k in obj
    )
    if not has_structure:
        return None

    message = _pick(obj, _MESSAGE_KEYS) or ""
    level = _pick(obj, _LEVEL_KEYS)
    timestamp = _pick(obj, _TIME_KEYS)
    consumed = {k.lower() for k in (*_MESSAGE_KEYS, *_TIME_KEYS, *_LEVEL_KEYS)}
    fields = {k: v for k, v in obj.items() if k.lower() not in consumed}
    return ParsedLine(message=message, level=level, timestamp=timestamp, fields=fields)


# "LEVEL timestamp message" and "timestamp LEVEL message" shapes, plus a bare
# "[LEVEL] message". One forgiving regex with named groups covers the common cases.
_LEVELED = re.compile(
    r"""
    ^\s*
    (?:\[?(?P<ts1>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?)\]?\s+)?  # optional leading ts
    \[?(?P<level>TRACE|DEBUG|INFO|INFORMATION|WARN|WARNING|ERROR|ERR|FATAL|CRITICAL)\]?
    \s+
    (?:(?P<ts2>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?)\s+)?         # optional trailing ts
    (?P<message>.*\S)\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def detect_leveled(line: str) -> Optional[ParsedLine]:
    """Plain text starting with (or led by) a recognizable level token."""
    m = _LEVELED.match(line)
    if not m:
        return None
    ts = m.group("ts1") or m.group("ts2")
    return ParsedLine(
        message=m.group("message"),
        level=m.group("level"),
        timestamp=ts,
    )


# Ordered most-specific to least-specific.
DETECTORS = (detect_json, detect_logfmt, detect_leveled)


# --------------------------------------------------------------------------- #
# Best-effort timestamp parsing. Never raises; returns None on failure.
# --------------------------------------------------------------------------- #

_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S",
)


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Try a few known formats. Return an aware UTC datetime, or None."""
    if not value:
        return None
    raw = value.strip()
    # Python's %z doesn't accept a literal 'Z'; normalize it to +0000.
    candidate = raw[:-1] + "+0000" if raw.endswith("Z") else raw
    for fmt in _TS_FORMATS:
        try:
            dt = datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None
