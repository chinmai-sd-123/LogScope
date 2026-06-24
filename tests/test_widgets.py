from datetime import datetime, timezone

from logscope.model import Level, LogEvent
from logscope.tui.widgets import matches_filter, render_event


def _event(message="hello", source="api", level=Level.INFO):
    return LogEvent(
        timestamp=datetime(2026, 1, 1, 12, 30, 45, tzinfo=timezone.utc),
        level=level,
        source=source,
        message=message,
        raw=message,
    )


def test_render_event_contains_key_parts():
    text = render_event(_event(message="db down", source="api", level=Level.ERROR))
    plain = text.plain
    assert "12:30:45" in plain
    assert "ERROR" in plain
    assert "[api]" in plain
    assert "db down" in plain


def test_matches_filter_substring():
    ev = _event(message="connection timeout", source="api")
    assert matches_filter(ev, "")            # empty filter matches all
    assert matches_filter(ev, "timeout")
    assert matches_filter(ev, "API")         # case-insensitive over source
    assert not matches_filter(ev, "zzz")
