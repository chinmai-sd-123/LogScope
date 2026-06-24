"""Headless smoke test of the TUI.

Uses Textual's ``run_test`` harness to mount the real app, drive its timers, and
assert that events produced by a source flow through the bounded queue into the
display buffer. We don't assert on pixels -- UI rendering is verified manually --
just that the producer/queue/consumer wiring is sound.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from logscope.model import Level, LogEvent
from logscope.tui.app import LogScopeApp


class FakeSource:
    """A source that emits a fixed list of events, then idles."""

    name = "fake"

    def __init__(self, events):
        self._events = events

    async def events(self, stop=None):
        for ev in self._events:
            yield ev
            await asyncio.sleep(0)
        # idle until told to stop so the producer task stays alive
        while stop is not None and not stop.is_set():
            await asyncio.sleep(0.01)


def _ev(message):
    return LogEvent(
        timestamp=datetime.now(timezone.utc),
        level=Level.INFO,
        source="fake",
        message=message,
        raw=message,
    )


@pytest.mark.asyncio
async def test_events_flow_into_buffer():
    events = [_ev("alpha"), _ev("bravo"), _ev("charlie")]
    app = LogScopeApp([FakeSource(events)])
    async with app.run_test() as pilot:
        await pilot.pause()
        await asyncio.sleep(0.25)  # let producer enqueue + drain timer fire
        await pilot.pause()
        messages = [e.message for e in app.buffer]
        assert messages == ["alpha", "bravo", "charlie"]


@pytest.mark.asyncio
async def test_filter_narrows_buffer_view():
    events = [_ev("connection timeout"), _ev("healthy"), _ev("timeout again")]
    app = LogScopeApp([FakeSource(events)])
    async with app.run_test() as pilot:
        await pilot.pause()
        await asyncio.sleep(0.25)
        await pilot.pause()
        # All three are buffered regardless of filter...
        assert len(app.buffer) == 3
        # ...and the filter predicate selects the right subset.
        from logscope.tui.widgets import matches_filter

        shown = [e.message for e in app.buffer if matches_filter(e, "timeout")]
        assert shown == ["connection timeout", "timeout again"]
