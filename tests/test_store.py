from datetime import datetime, timedelta, timezone

import pytest

from logscope.index.store import EventStore, event_id
from logscope.model import Level, LogEvent
from logscope.query.parser import parse_query


def _ev(message, *, source="api", level=Level.INFO, age_s=0, fields=None):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return LogEvent(
        timestamp=ts, level=level, source=source,
        message=message, raw=message, fields=fields or {},
    )


@pytest.fixture
def store():
    s = EventStore(":memory:")
    yield s
    s.close()


def test_add_and_count(store):
    store.add_many([_ev("a"), _ev("b"), _ev("c")])
    assert store.count() == 3


def test_idempotent_insert(store):
    ev = _ev("dup", source="api")
    store.add(ev)
    store.add(ev)  # identical -> same event_id -> ignored
    assert store.count() == 1


def test_search_free_text_via_fts(store):
    store.add_many([
        _ev("connection timeout to db"),
        _ev("user logged in"),
        _ev("request timeout after retries"),
    ])
    results = store.search(parse_query('"timeout"'))
    messages = {r.message for r in results}
    assert messages == {"connection timeout to db", "request timeout after retries"}


def test_search_level_and_source(store):
    store.add_many([
        _ev("boom", source="api", level=Level.ERROR),
        _ev("boom", source="web", level=Level.ERROR),
        _ev("fine", source="api", level=Level.INFO),
    ])
    results = store.search(parse_query("level:error source:api"))
    assert len(results) == 1
    assert results[0].source == "api" and results[0].level == Level.ERROR


def test_search_combines_structured_and_text(store):
    store.add_many([
        _ev("db timeout", source="api", level=Level.ERROR),
        _ev("db timeout", source="web", level=Level.ERROR),
        _ev("healthy", source="api", level=Level.ERROR),
    ])
    results = store.search(parse_query('level:error source:api "timeout"'))
    assert len(results) == 1
    assert results[0].message == "db timeout" and results[0].source == "api"


def test_search_time_window(store):
    store.add_many([_ev("recent", age_s=10), _ev("old", age_s=3600)])
    results = store.search(parse_query("last:1m"))
    assert {r.message for r in results} == {"recent"}


def test_results_newest_first(store):
    store.add(_ev("older", age_s=100))
    store.add(_ev("newer", age_s=1))
    results = store.search(parse_query(""))
    assert [r.message for r in results] == ["newer", "older"]


def test_fields_round_trip(store):
    store.add(_ev("with fields", fields={"request_id": "a1", "n": 5}))
    (result,) = store.search(parse_query('"with fields"'))
    assert result.fields["request_id"] == "a1"


def test_event_id_is_stable_and_distinct():
    a = event_id("api", "line one", 1000)
    b = event_id("api", "line one", 1000)
    c = event_id("api", "line two", 1000)
    assert a == b and a != c
