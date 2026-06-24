"""Presentation helpers for the TUI.

Kept separate from the app so the (pure) formatting logic can be unit tested
without spinning up a Textual app.
"""

from __future__ import annotations

from rich.text import Text

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
