from datetime import datetime, timezone

import pytest

from logscope.model import Level
from logscope.parse.parser import Parser, normalize_level

parser = Parser()


# --------------------------------------------------------------------------- #
# JSON lines
# --------------------------------------------------------------------------- #


def test_json_line():
    line = '{"level": "error", "msg": "db down", "ts": "2026-01-02T03:04:05Z", "request_id": "a1"}'
    ev = parser.parse(line, source="api")
    assert ev.level == Level.ERROR
    assert ev.message == "db down"
    assert ev.source == "api"
    assert ev.fields["request_id"] == "a1"
    assert ev.timestamp.year == 2026 and ev.timestamp.tzinfo is not None
    assert ev.raw == line  # raw is always preserved


def test_json_with_message_key_variants():
    ev = parser.parse('{"severity": "WARN", "message": "slow query"}')
    assert ev.level == Level.WARN
    assert ev.message == "slow query"


# --------------------------------------------------------------------------- #
# logfmt
# --------------------------------------------------------------------------- #


def test_logfmt_line():
    ev = parser.parse('level=info msg="user logged in" user_id=42')
    assert ev.level == Level.INFO
    assert ev.message == "user logged in"
    assert ev.fields["user_id"] == "42"


def test_logfmt_does_not_misfire_on_prose():
    # A sentence that merely contains "x=y" but no structural keys stays unstructured.
    ev = parser.parse("the variable a=b was unexpected")
    assert ev.message == "the variable a=b was unexpected"


# --------------------------------------------------------------------------- #
# Leveled plain text
# --------------------------------------------------------------------------- #


def test_leveled_plain_text():
    ev = parser.parse("ERROR 2026-01-02T03:04:05 connection refused")
    assert ev.level == Level.ERROR
    assert ev.message == "connection refused"
    assert ev.timestamp.year == 2026


def test_bracketed_level():
    ev = parser.parse("[WARN] disk almost full")
    assert ev.level == Level.WARN
    assert ev.message == "disk almost full"


# --------------------------------------------------------------------------- #
# Fallback + robustness (the non-negotiable property of a log tool)
# --------------------------------------------------------------------------- #


def test_unstructured_line_scans_for_level():
    ev = parser.parse("something went wrong: fatal error in worker")
    assert ev.level == Level.FATAL  # scanned from the message


def test_plain_line_defaults_to_info():
    ev = parser.parse("just a normal line")
    assert ev.level == Level.INFO
    assert ev.message == "just a normal line"


def test_unparseable_timestamp_falls_back_to_ingest_time():
    fixed = datetime(2020, 5, 5, tzinfo=timezone.utc)
    ev = parser.parse('{"msg": "x", "ts": "not-a-date"}', ingest_ts=fixed)
    assert ev.timestamp == fixed
    assert ev.ingest_ts == fixed


@pytest.mark.parametrize(
    "garbage",
    ["", "   ", "{", "}{", '{"unterminated":', "\x00\x01\x02", "level=", "ä" * 500],
)
def test_never_raises_on_garbage(garbage):
    # The robustness proof: any input returns a LogEvent, never an exception.
    ev = parser.parse(garbage)
    assert ev is not None
    assert ev.raw == garbage
    assert isinstance(ev.level, Level)


# --------------------------------------------------------------------------- #
# Level normalization
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "token,expected",
    [
        ("ERROR", Level.ERROR),
        ("err", Level.ERROR),
        ("E", Level.ERROR),
        ("warning", Level.WARN),
        ("critical", Level.FATAL),
        ("information", Level.INFO),
        ("7", Level.FATAL),
        ("bogus", None),
    ],
)
def test_normalize_level(token, expected):
    assert normalize_level(token) == expected
