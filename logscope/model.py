"""The core data model.

A :class:`LogEvent` is the immutable unit that flows through the entire
pipeline: ingest -> parse -> (index | cluster | anomaly) -> sinks.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import IntEnum


class Level(IntEnum):
    """Normalized severity.

    ``IntEnum`` so severities compare naturally: ``level >= Level.ERROR`` means
    "this severity or worse". Downstream code never deals with format-specific
    level strings -- the parser normalizes everything to this enum.
    """

    TRACE = 0
    DEBUG = 1
    INFO = 2
    WARN = 3
    ERROR = 4
    FATAL = 5


@dataclass(frozen=True, slots=True)
class LogEvent:
    """One parsed log line.

    Immutable (``frozen``) so it is safe to share across coroutines without
    locks, and slotted so millions of instances stay memory-cheap.
    """

    timestamp: datetime              # event time (UTC); falls back to ingest time if unknown
    level: Level                     # normalized severity
    source: str                      # which file/service/agent produced this line
    message: str                     # the human-readable body
    raw: str                         # original unparsed line -- ALWAYS kept
    fields: dict = field(default_factory=dict)   # extracted key/values (request_id, etc.)
    ingest_ts: datetime | None = None            # when LogScope first saw it (for lag)
    template_id: int | None = None               # set later by the clustering engine

    def with_template(self, template_id: int) -> LogEvent:
        """Return a new event with ``template_id`` set.

        Events are frozen, so clustering produces a copy rather than mutating.
        """
        return replace(self, template_id=template_id)
