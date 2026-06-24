"""The Textual app: live stream + cluster panel + error-rate sparkline.

Architecture seam worth pointing at: the TUI never reads files. Producer
coroutines tail sources and push :class:`LogEvent`s onto a *bounded* queue; the
UI drains that queue on a timer. The bound gives backpressure for free, and the
decoupling means a network source can later replace the file source without the
UI changing at all.

Each drained event fans out to three independent consumers -- the optional
store (history), the Drain miner (clusters), and the anomaly detector (spikes) --
exactly mirroring the processing stage of the pipeline.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Iterable

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Sparkline, Static

from logscope.anomaly.detector import AnomalyDetector
from logscope.cluster.drain import Drain
from logscope.index.store import EventStore
from logscope.ingest.source import Source
from logscope.model import Level, LogEvent
from logscope.tui.widgets import matches_filter, render_cluster_table, render_event

# Bounded so a firehose source can never balloon memory: a full queue makes the
# producer await (backpressure). The ring buffer caps what we keep for redraws.
QUEUE_MAXSIZE = 1000
BUFFER_SIZE = 2000
DRAIN_INTERVAL = 0.1   # seconds: how often we pull from the queue
REFRESH_INTERVAL = 1.0  # seconds: how often we redraw clusters + sparkline


class LogScopeApp(App):
    CSS = """
    #body { height: 1fr; }
    #stream { width: 2fr; border: round $primary; }
    #side { width: 1fr; }
    #clusters { height: 1fr; border: round $secondary; }
    #spark { height: 5; border: round $warning; }
    #filter { dock: bottom; border: tall $accent; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(
        self, sources: Iterable[Source], store: EventStore | None = None
    ) -> None:
        super().__init__()
        self.sources = list(sources)
        self.store = store
        self.drain = Drain()
        # Error-rate detector: counts ERROR+ events per bucket for the sparkline.
        self.detector = AnomalyDetector(bucket_seconds=2, window=30, k=3.0, min_count=5)

        self.queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.buffer: deque[LogEvent] = deque(maxlen=BUFFER_SIZE)
        self.filter_text = ""
        self._spark_data: list[int] = [0]
        self._stop = asyncio.Event()
        self._producers: list[asyncio.Task] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield RichLog(id="stream", highlight=False, markup=False, wrap=False)
            with Vertical(id="side"):
                yield Static(id="clusters")
                yield Sparkline([0], id="spark", summary_function=max)
        yield Input(placeholder="filter (substring over message/source)…", id="filter")
        yield Footer()

    async def on_mount(self) -> None:
        for source in self.sources:
            self._producers.append(asyncio.create_task(self._produce(source)))
        self.set_interval(DRAIN_INTERVAL, self._drain)
        self.set_interval(REFRESH_INTERVAL, self._refresh_panels)
        self.query_one("#filter", Input).focus()

    async def _produce(self, source: Source) -> None:
        try:
            async for event in source.events(stop=self._stop):
                await self.queue.put(event)  # await on full queue == backpressure
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
                self.store.add(event)
            self.drain.add_message(event.message)
            if event.level >= Level.ERROR:
                bucket = self.detector.add(int(event.timestamp.timestamp() * 1000))
                if bucket is not None:
                    self._spark_data = self.detector.recent_counts() or [0]

            if matches_filter(event, self.filter_text):
                log.write(render_event(event))
                wrote = True
        if wrote:
            log.scroll_end(animate=False)

    def _refresh_panels(self) -> None:
        self.query_one("#clusters", Static).update(
            render_cluster_table(self.drain.templates)
        )
        self.query_one("#spark", Sparkline).data = self._spark_data or [0]

    def on_input_changed(self, message: Input.Changed) -> None:
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
            self.store.close()
        self.exit()
