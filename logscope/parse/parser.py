"""The parser: a raw line in, a LogEvent out.

Parser.parse never raises and always returns an event, even on garbage input --
malformed lines are common in real logs.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from logscope.model import Level, LogEvent
from logscope.parse.formats import DETECTORS, parse_timestamp

# Map the many spellings of severity onto our normalized enum. Normalizing at
# the boundary means no downstream code ever sees a format-specific level token.
_LEVEL_ALIASES: dict[str, Level] = {
    "trace": Level.TRACE,
    "debug": Level.DEBUG,
    "info": Level.INFO,
    "information": Level.INFO,
    "notice": Level.INFO,
    "warn": Level.WARN,
    "warning": Level.WARN,
    "error": Level.ERROR,
    "err": Level.ERROR,
    "fatal": Level.FATAL,
    "critical": Level.FATAL,
    "crit": Level.FATAL,
    "emergency": Level.FATAL,
}

# Single-letter severities (Go/glog style: I, W, E, F).
_LEVEL_LETTERS: dict[str, Level] = {
    "t": Level.TRACE,
    "d": Level.DEBUG,
    "i": Level.INFO,
    "w": Level.WARN,
    "e": Level.ERROR,
    "f": Level.FATAL,
}

# Last-resort scan of an unstructured message for a severity word.
_LEVEL_SCAN = re.compile(
    r"\b(fatal|critical|error|warn(?:ing)?|debug|trace)\b", re.IGNORECASE
)


def normalize_level(value: Optional[str]) -> Optional[Level]:
    """Map a level token of any common spelling onto :class:`Level`."""
    if value is None:
        return None
    token = value.strip().lower()
    if token in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[token]
    if len(token) == 1 and token in _LEVEL_LETTERS:
        return _LEVEL_LETTERS[token]
    # Numeric severities (e.g. syslog 0-7): clamp into our range heuristically.
    if token.isdigit():
        n = int(token)
        if n >= 5:
            return Level.FATAL
        return Level(n) if 0 <= n <= 5 else None
    return None


class Parser:
    """Stateless format-detecting parser.

    Stateless so it is trivially safe to share across coroutines and to unit
    test. Construct once, call :meth:`parse` per line.
    """

    def parse(
        self,
        raw: str,
        source: str = "-",
        ingest_ts: Optional[datetime] = None,
    ) -> LogEvent:
        """Parse one raw line into a :class:`LogEvent`. Never raises."""
        ingest_ts = ingest_ts or datetime.now(timezone.utc)

        message = raw
        level: Optional[Level] = None
        timestamp: Optional[datetime] = None
        fields: dict = {}

        try:
            for detect in DETECTORS:
                parsed = detect(raw)
                if parsed is None:
                    continue
                message = parsed.message or raw
                level = normalize_level(parsed.level)
                timestamp = parse_timestamp(parsed.timestamp)
                fields = parsed.fields
                break
        except Exception:
            # Detection failed; fall through to the unstructured path.
            message, level, timestamp, fields = raw, None, None, {}

        # Fallbacks: a level scan on the message, and ingest time for the clock.
        if level is None:
            level = self._scan_level(message)
        if timestamp is None:
            timestamp = ingest_ts

        return LogEvent(
            timestamp=timestamp,
            level=level if level is not None else Level.INFO,
            source=source,
            message=message,
            raw=raw,
            fields=fields,
            ingest_ts=ingest_ts,
        )

    @staticmethod
    def _scan_level(message: str) -> Optional[Level]:
        """Heuristic: look for a severity word in an otherwise unstructured line."""
        m = _LEVEL_SCAN.search(message)
        if not m:
            return None
        return normalize_level(m.group(1))
