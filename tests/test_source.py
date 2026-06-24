"""FileSource ties the tailer to the parser and labels events with a source."""

import asyncio

import pytest

from logscope.ingest.source import FileSource
from logscope.model import Level


@pytest.mark.asyncio
async def test_file_source_parses_and_labels(tmp_path):
    f = tmp_path / "api.log"
    f.write_text(
        '{"level":"error","msg":"db down"}\n'
        "INFO plain info line\n",
        encoding="utf-8",
    )

    stop = asyncio.Event()
    events = []

    async def run():
        async for ev in FileSource(f, from_start=True).events(stop=stop):
            events.append(ev)
            if len(events) >= 2:
                stop.set()
                return

    await asyncio.wait_for(run(), timeout=3.0)

    assert events[0].level == Level.ERROR
    assert events[0].message == "db down"
    assert events[0].source == "api.log"        # labelled from the filename
    assert events[1].level == Level.INFO
    assert events[1].ingest_ts is not None       # ingest layer stamps the clock
