"""Presentation helpers for the TUI.

Kept separate from the app so the (pure) formatting logic can be unit tested
without spinning up a Textual app.
"""

from __future__ import annotations

from typing import Sequence

from rich.table import Table
from rich.text import Text

from logscope.cluster.drain import Template
from logscope.model import Level, LogEvent

# Per-level styling. Errors shout, debug/trace recede.
_LEVEL_STYLE: dict[Level, str] = {
    Level.TRACE: "dim",
    Level.DEBUG: "dim cyan",
    Level.INFO: "white",
    Level.WARN: "yellow",
    Level.ERROR: "bold red",
    Level.FATAL: "bold white on red",
}


def render_event(event: LogEvent) -> Text:
    """Render one event as a colorized single line for the stream pane."""
    style = _LEVEL_STYLE.get(event.level, "white")
    ts = event.timestamp.strftime("%H:%M:%S")
    text = Text()
    text.append(f"{ts} ", style="dim")
    text.append(f"{event.level.name:<5} ", style=style)
    text.append(f"[{event.source}] ", style="dim blue")
    text.append(event.message, style=style)
    return text


def matches_filter(event: LogEvent, needle: str) -> bool:
    """Case-insensitive substring filter over the message and source.

    Phase 1 uses a plain substring; Phase 2 replaces this with the real query
    language compiled to a live-stream predicate.
    """
    if not needle:
        return True
    needle = needle.lower()
    return needle in event.message.lower() or needle in event.source.lower()


def render_cluster_table(templates: Sequence[Template], top_n: int = 12) -> Table:
    """Render the top templates as a ranked count + bar + template table."""
    table = Table(expand=True, show_edge=False, pad_edge=False, box=None, padding=(0, 1, 0, 0))
    table.add_column("count", justify="right", width=7)
    table.add_column("", width=8)  # proportional bar
    table.add_column("template", no_wrap=True, overflow="ellipsis", ratio=1)

    top = list(templates[:top_n])
    max_count = max((t.count for t in top), default=1) or 1
    for template in top:
        bar_len = round(8 * template.count / max_count)
        count = Text(f"{template.count:,}", style="bold cyan")
        bar = Text("█" * bar_len, style="cyan")
        table.add_row(count, bar, template.as_string())
    return table
