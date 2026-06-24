from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from logscope.model import Level, LogEvent


def _sample() -> LogEvent:
    return LogEvent(
        timestamp=datetime.now(timezone.utc),
        level=Level.INFO,
        source="test",
        message="hello",
        raw="hello",
    )


def test_level_ordering():
    # The point of IntEnum: severity comparison.
    assert Level.ERROR >= Level.WARN
    assert Level.INFO < Level.ERROR


def test_event_is_immutable():
    ev = _sample()
    with pytest.raises(FrozenInstanceError):
        ev.level = Level.ERROR  # type: ignore[misc]


def test_with_template_returns_new_event():
    ev = _sample()
    ev2 = ev.with_template(42)
    assert ev.template_id is None       # original untouched
    assert ev2.template_id == 42        # copy carries the id
    assert ev is not ev2                # genuinely a new object
