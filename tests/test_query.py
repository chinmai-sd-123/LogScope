from datetime import datetime, timedelta, timezone

import pytest

from logscope.model import Level, LogEvent
from logscope.query.ast import (
    FieldTerm,
    LevelTerm,
    SourceTerm,
    TextTerm,
    TimeTerm,
)
from logscope.query.lexer import LexError, TokenType, tokenize
from logscope.query.parser import ParseError, parse_query

# --------------------------------------------------------------------------- #
# Lexer
# --------------------------------------------------------------------------- #


def test_lexer_basic_tokens():
    toks = tokenize('level:error "connection timeout"')
    types = [t.type for t in toks]
    assert types == [
        TokenType.IDENT, TokenType.COLON, TokenType.IDENT,
        TokenType.STRING, TokenType.EOF,
    ]
    assert toks[3].value == "connection timeout"


def test_lexer_unterminated_string():
    with pytest.raises(LexError):
        tokenize('msg:"oops')


# --------------------------------------------------------------------------- #
# Parser -> AST shapes
# --------------------------------------------------------------------------- #


def test_parse_empty_matches_all():
    q = parse_query("")
    assert q.terms == []
    assert q.to_predicate()(_ev()) is True


def test_parse_full_query_shapes():
    q = parse_query('level:error source:api last:15m request_id:a1 "timeout"')
    assert any(isinstance(t, LevelTerm) and t.level == Level.ERROR for t in q.terms)
    assert any(isinstance(t, SourceTerm) and t.source == "api" for t in q.terms)
    assert any(isinstance(t, TimeTerm) and t.seconds == 900 for t in q.terms)
    assert any(isinstance(t, FieldTerm) and t.key == "request_id" for t in q.terms)
    assert any(isinstance(t, TextTerm) and t.text == "timeout" for t in q.terms)


def test_parse_bare_word_is_free_text():
    q = parse_query("timeout")
    assert q.terms == [TextTerm("timeout")]


@pytest.mark.parametrize(
    "bad",
    ["level:bogus", "last:5x", "last:abc", "source:"],
)
def test_parse_errors(bad):
    with pytest.raises(ParseError):
        parse_query(bad)


# --------------------------------------------------------------------------- #
# Live predicate (one of the two evaluation targets)
# --------------------------------------------------------------------------- #


def _ev(message="hello", source="api", level=Level.INFO, fields=None, age_s=0):
    ts = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    return LogEvent(
        timestamp=ts, level=level, source=source,
        message=message, raw=message, fields=fields or {},
    )


def test_predicate_level_is_threshold():
    pred = parse_query("level:warn").to_predicate()
    assert pred(_ev(level=Level.ERROR))   # error >= warn
    assert pred(_ev(level=Level.WARN))
    assert not pred(_ev(level=Level.INFO))


def test_predicate_anded():
    pred = parse_query('level:error source:api "down"').to_predicate()
    assert pred(_ev(message="db is down", source="api", level=Level.ERROR))
    assert not pred(_ev(message="db is down", source="web", level=Level.ERROR))
    assert not pred(_ev(message="all healthy", source="api", level=Level.ERROR))


def test_predicate_time_window():
    pred = parse_query("last:1m").to_predicate()
    assert pred(_ev(age_s=10))       # within the last minute
    assert not pred(_ev(age_s=120))  # two minutes ago


def test_predicate_field_match():
    pred = parse_query("request_id:a1").to_predicate()
    assert pred(_ev(fields={"request_id": "a1"}))
    assert not pred(_ev(fields={"request_id": "zz"}))
