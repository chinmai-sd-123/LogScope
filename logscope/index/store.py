"""Event persistence and search backed by SQLite + FTS5.

SQLite ships with Python and includes FTS5, giving indexed full-text search with
zero external services -- right-sized for a single-node tool. Design points:

* **Batched writes.** Events buffer in memory and flush in one transaction, the
  difference between thousands and tens of thousands of inserts/second.
* **External-content FTS.** ``content='events'`` makes the FTS index reference
  the base table instead of duplicating message text; triggers keep them synced.
* **Idempotent inserts.** Each event has a stable ``event_id`` (hash of
  source+raw+timestamp) with ``INSERT OR IGNORE``, so re-ingesting a duplicate
  (e.g. an agent resend in Phase 4) is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from logscope.model import Level, LogEvent
from logscope.query.ast import Query

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY,
    event_id    TEXT UNIQUE,
    ts          INTEGER NOT NULL,          -- epoch millis, for range scans
    level       INTEGER NOT NULL,
    source      TEXT NOT NULL,
    message     TEXT NOT NULL,
    raw         TEXT NOT NULL,
    fields      TEXT,                       -- JSON blob
    template_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_level ON events(level);

CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    message, source, content='events', content_rowid='id'
);

-- Keep the external-content FTS index in sync with the base table.
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, message, source)
    VALUES (new.id, new.message, new.source);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, message, source)
    VALUES ('delete', old.id, old.message, old.source);
END;
"""


def event_id(source: str, raw: str, ts_ms: int) -> str:
    """Stable id for de-duplication: a hash of the identifying fields."""
    h = hashlib.sha1(f"{source}|{raw}|{ts_ms}".encode("utf-8", "replace"))
    return h.hexdigest()


def _to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class EventStore:
    """A SQLite-backed store with full-text search."""

    def __init__(self, path: Path | str = ":memory:", *, batch_size: int = 500) -> None:
        self.path = str(path)
        self.batch_size = batch_size
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        # WAL improves concurrent read/write throughput; harmless for :memory:.
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._buffer: List[tuple] = []

    # -- writes ------------------------------------------------------------ #

    def add(self, event: LogEvent) -> None:
        """Buffer one event; flushes automatically at ``batch_size``."""
        ts_ms = _to_ms(event.timestamp)
        self._buffer.append(
            (
                event_id(event.source, event.raw, ts_ms),
                ts_ms,
                int(event.level),
                event.source,
                event.message,
                event.raw,
                json.dumps(event.fields, default=str) if event.fields else None,
                event.template_id,
            )
        )
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def add_many(self, events: Iterable[LogEvent]) -> None:
        for ev in events:
            self.add(ev)

    def flush(self) -> None:
        """Write buffered events in a single transaction (the throughput lever)."""
        if not self._buffer:
            return
        with self._conn:  # transaction
            self._conn.executemany(
                """INSERT OR IGNORE INTO events
                   (event_id, ts, level, source, message, raw, fields, template_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                self._buffer,
            )
        self._buffer.clear()

    # -- reads ------------------------------------------------------------- #

    def search(self, query: Query, *, limit: int = 100) -> List[LogEvent]:
        """Compile the AST to SQL + FTS and return matching events, newest first."""
        self.flush()  # make buffered events searchable

        where: List[str] = []
        params: List = []

        # Non-text terms compile straight to WHERE fragments.
        for term in query.non_text_terms():
            frag, frag_params = term.to_sql()
            where.append(frag)
            params.extend(frag_params)

        # Free-text terms compile to a single FTS MATCH subquery (AND-ed).
        text_terms = query.text_terms()
        if text_terms:
            # Wrap each term as an FTS phrase; double internal quotes so a term
            # containing '"' can't break out of the phrase and corrupt the query.
            match = " ".join(f'"{t.text.replace(chr(34), chr(34) * 2)}"' for t in text_terms)
            where.append("id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)")
            params.append(match)

        sql = "SELECT * FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count(self) -> int:
        self.flush()
        return self._conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LogEvent:
        return LogEvent(
            timestamp=_from_ms(row["ts"]),
            level=Level(row["level"]),
            source=row["source"],
            message=row["message"],
            raw=row["raw"],
            fields=json.loads(row["fields"]) if row["fields"] else {},
            template_id=row["template_id"],
        )

    # -- lifecycle --------------------------------------------------------- #

    def close(self) -> None:
        self.flush()
        self._conn.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
