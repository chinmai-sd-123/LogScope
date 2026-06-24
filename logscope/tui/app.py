"""The Phase 1 Textual app: a live, colorized, filterable log stream.

Architecture seam worth pointing at: the TUI never reads files. Producer
coroutines tail sources and push :class:`LogEvent`s onto a *bounded* queue; the
UI drains that queue on a timer. The bound gives backpressure for free, and the
decoupling means a network source can later replace the file source without the
UI changing at all.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Iterable

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog

from logscope.index.store import EventStore
from logscope.ingest.source import Source
from logscope.model import LogEvent
from logscope.tui.widgets import matches_filter, render_event

# Bounded so a firehose source can never balloon memory: a full queue makes the
# producer await (backpressure). The ring buffer caps what we keep for redraws.
QUEUE_MAXSIZE = 1000
BUFFER_SIZE = 2000
DRAIN_INTERVAL = 0.1  # seconds


class LogScopeApp(App):
    CSS = """
    #stream { height: 1fr; border: round $primary; }
    #filter { dock: bottom; border: tall $accent; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(
        self, sources: Iterable[Source], store: EventStore | None = None
    ) -> None:
        super().__init__()
        self.sources = list(sources)
        self.store = store  # optional: persist tailed events for later search
        self.queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.buffer: deque[LogEvent] = deque(maxlen=BUFFER_SIZE)
        self.filter_text = ""
        self._stop = asyncio.Event()
        self._producers: list[asyncio.Task] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield RichLog(id="stream", highlight=False, markup=False, wrap=False)
            yield Input(placeholder="filter (substring over message/source)…", id="filter")
        yield Footer()

    async def on_mount(self) -> None:
        # One producer task per source. Each iterates its events and enqueues
        # them; await on a full queue is the backpressure.
        for source in self.sources:
            self._producers.append(asyncio.create_task(self._produce(source)))
        # Drain the queue into the UI on a timer rather than per-event, so a
        # burst of events becomes one batched redraw.
        self.set_interval(DRAIN_INTERVAL, self._drain)
        self.query_one("#filter", Input).focus()

    async def _produce(self, source: Source) -> None:
        try:
            async for event in source.events(stop=self._stop):
                await self.queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a bad source must not crash the app
            self.query_one("#stream", RichLog).write(
                f"[source {source.name} error: {exc!r}]"
            )

    def _drain(self) -> None:
        log = self.query_one("#stream", RichLog)
        wrote = False
        while True:
            try:
                event = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self.buffer.append(event)
            if self.store is not None:
                self.store.add(event)  # buffered + batch-flushed internally
            if matches_filter(event, self.filter_text):
                log.write(render_event(event))
                wrote = True
        if wrote:
            log.scroll_end(animate=False)

    def on_input_changed(self, message: Input.Changed) -> None:
        # Re-render the whole buffer through the new filter.
        self.filter_text = message.value
        log = self.query_one("#stream", RichLog)
        log.clear()
        for event in self.buffer:
            if matches_filter(event, self.filter_text):
                log.write(render_event(event))

    async def action_quit(self) -> None:
        self._stop.set()
        for task in self._producers:
            task.cancel()
        if self.store is not None:
            self.store.close()  # flush buffered events to disk
        self.exit()
