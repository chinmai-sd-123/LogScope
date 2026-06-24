"""Source abstraction: anything that produces LogEvents.

A source ties a stream of raw lines to a Parser and labels each event with its
origin. Downstream stages only see LogEvents, so a file source can be swapped for
a network source without changing them.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional, Protocol

from logscope.ingest.tailer import tail
from logscope.model import LogEvent
from logscope.parse.parser import Parser


class Source(Protocol):
    """Anything that yields parsed events."""

    name: str

    def events(self, stop: Optional[asyncio.Event] = None) -> AsyncIterator[LogEvent]:
        ...


class FileSource:
    """A source backed by a tailed file."""

    def __init__(
        self,
        path: Path | str,
        *,
        parser: Optional[Parser] = None,
        from_start: bool = False,
        name: Optional[str] = None,
    ) -> None:
        self.path = Path(path)
        self.parser = parser or Parser()
        self.from_start = from_start
        # The source label defaults to the file's basename (e.g. "app.log").
        self.name = name or self.path.name

    async def events(
        self, stop: Optional[asyncio.Event] = None
    ) -> AsyncIterator[LogEvent]:
        async for line in tail(
            self.path, from_start=self.from_start, stop=stop
        ):
            if not line:
                continue
            ingest_ts = datetime.now(timezone.utc)
            yield self.parser.parse(line, source=self.name, ingest_ts=ingest_ts)
