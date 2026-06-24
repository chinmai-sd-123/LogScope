"""The Textual app: live stream + cluster panel + error-rate sparkline.

Producer coroutines tail sources and push events onto a bounded queue; the UI
drains it on a timer. The TUI never reads files directly, so the source can be
swapped without UI changes. Each drained event is fed to the store, the Drain
miner, and the anomaly detector.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Iterable

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, RichLog, Sparkline, Static

from logscope.ai.cache import SummaryCache
from logscope.ai.summarizer import (
    ClusterContext,
    NullSummarizer,
    Summarizer,
    summarize_cluster,
)
from logscope.anomaly.detector import AnomalyDetector
from logscope.cluster.drain import Drain
from logscope.index.store import EventStore
from logscope.ingest.source import Source
from logscope.metrics import Metrics
from logscope.model import Level, LogEvent
from logscope.tui.widgets import matches_filter, render_cluster_table, render_event

# Bounded queue gives backpressure (producer awaits when full); the ring buffer
# caps what we keep in memory for redraws.
QUEUE_MAXSIZE = 1000
BUFFER_SIZE = 2000
DRAIN_INTERVAL = 0.1   # seconds: how often we pull from the queue
REFRESH_INTERVAL = 1.0  # seconds: how often we redraw clusters + sparkline


class SummaryScreen(ModalScreen):
    """Full-screen, scrollable popup with a cluster's AI root-cause summary.

    A modal so a multi-paragraph summary is fully readable and the moving log
    stream doesn't interfere while reading it.
    """

    CSS = """
    SummaryScreen { align: center middle; background: $background 60%; }
    #summary-box {
        width: 80%; max-width: 100; height: 80%;
        padding: 1 2; border: round $success; background: $panel;
    }
    #summary-title { text-style: bold; color: $success; margin-bottom: 1; }
    #summary-hint { color: $text-muted; margin-top: 1; }
    """
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(
        self, ctx: ClusterContext, summarizer: Summarizer, cache: SummaryCache
    ) -> None:
        super().__init__()
        self.ctx = ctx
        self.summarizer = summarizer
        self.cache = cache
        self.body_text = ""  # mirror of the body, for tests

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="summary-box"):
            yield Static(
                f"Cluster ({self.ctx.count:,}x):  {self.ctx.template}", id="summary-title"
            )
            yield Static("", id="summary-body")
            yield Static("[esc] close", id="summary-hint")

    async def on_mount(self) -> None:
        if not self.summarizer.enabled:
            self._set("AI summary unavailable. Set OPENAI_API_KEY in .env to enable it.")
            return
        self._set("summarizing… (one moment)")
        self.run_worker(self._load(), exclusive=True)

    async def _load(self) -> None:
        summary = await summarize_cluster(self.ctx, self.summarizer, self.cache)
        self._set(summary or "summary unavailable (provider error or timeout)")

    def _set(self, text: str) -> None:
        self.body_text = text
        self.query_one("#summary-body", Static).update(text)

    def action_close(self) -> None:
        self.dismiss()


class LogScopeApp(App):
    TITLE = "LogScope"
    CSS = """
    Screen { background: $surface; }
    #body { height: 1fr; padding: 0 1; }
    #stream {
        width: 2fr; min-width: 30; border: round $primary;
        background: $panel; padding: 0 1;
    }
    #side { width: 1fr; min-width: 34; }
    #clusters { height: 1fr; border: round $secondary; background: $panel; padding: 0 1; }
    #spark { height: 6; border: round $warning; padding: 1 1; }
    #filter { dock: bottom; border: tall $accent; }
    """
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+s", "summarize", "AI summary"),
        ("f2", "toggle_pause", "Pause/Resume"),
    ]

    def __init__(
        self,
        sources: Iterable[Source],
        store: EventStore | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        super().__init__()
        self.sources = list(sources)
        self.store = store
        self.summarizer: Summarizer = summarizer or NullSummarizer()
        self.cache = SummaryCache()
        self.metrics = Metrics()
        self.drain = Drain()
        # A few representative raw lines per template, for AI grounding context.
        self._samples: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=20))
        self.paused = False  # freeze the stream pane for reading (f2)
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
        # Titled borders make each pane self-explanatory.
        self.query_one("#stream").border_title = "Live stream"
        self.query_one("#clusters").border_title = "Clusters (ranked by volume)"
        self.query_one("#spark").border_title = "Error rate"
        self.sub_title = "starting…"

        for source in self.sources:
            self._producers.append(asyncio.create_task(self._produce(source)))
        self.set_interval(DRAIN_INTERVAL, self._drain)
        self.set_interval(REFRESH_INTERVAL, self._refresh_panels)
        self.query_one("#filter", Input).focus()

    async def _produce(self, source: Source) -> None:
        try:
            async for event in source.events(stop=self._stop):
                await self.queue.put(event)  # blocks when full (backpressure)
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

            # Cluster first, then tag the event with its template id so the
            # stored row records which cluster it belongs to.
            template = self.drain.add_message(event.message)
            event = event.with_template(template.id)
            self._samples[template.id].append(event.raw)

            self.buffer.append(event)
            if self.store is not None:
                self.store.add(event)

            now_ms = event.ingest_ts.timestamp() * 1000 if event.ingest_ts else 0
            lag_ms = max(0.0, now_ms - event.timestamp.timestamp() * 1000)
            self.metrics.record_event(lag_ms)

            if event.level >= Level.ERROR:
                bucket = self.detector.add(int(event.timestamp.timestamp() * 1000))
                if bucket is not None:
                    self._spark_data = self.detector.recent_counts() or [0]

            if not self.paused and matches_filter(event, self.filter_text):
                log.write(render_event(event))
                wrote = True
        if wrote:
            log.scroll_end(animate=False)

    def _refresh_panels(self) -> None:
        self.query_one("#clusters", Static).update(
            render_cluster_table(self.drain.templates)
        )
        self.query_one("#spark", Sparkline).data = self._spark_data or [0]
        # Update self-metrics and surface them in the header.
        self.metrics.queue_depth.set(self.queue.qsize())
        self.metrics.cluster_count.set(len(self.drain.templates))
        self.sub_title = self.metrics.status_line(ai_hit_rate=self.cache.hit_rate)

    def on_input_changed(self, message: Input.Changed) -> None:
        self.filter_text = message.value
        log = self.query_one("#stream", RichLog)
        log.clear()
        for event in self.buffer:
            if matches_filter(event, self.filter_text):
                log.write(render_event(event))

    def action_summarize(self) -> None:
        """Open a scrollable popup with the top cluster's AI summary."""
        templates = self.drain.templates
        if not templates:
            self.notify("No clusters yet.")
            return
        top = templates[0]
        ctx = ClusterContext(
            template=top.as_string(),
            count=top.count,
            sample_lines=list(self._samples.get(top.id, [])),
        )
        self.push_screen(SummaryScreen(ctx, self.summarizer, self.cache))

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        title = "Live stream  ⏸ PAUSED" if self.paused else "Live stream"
        self.query_one("#stream").border_title = title

    async def action_quit(self) -> None:
        self._stop.set()
        for task in self._producers:
            task.cancel()
        if self.store is not None:
            self.store.close()
        self.exit()
