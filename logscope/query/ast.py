"""The query AST.

A :class:`Query` is a conjunction (AND) of independent terms. Each term knows
how to render itself two ways: as a SQL fragment (for history) and as a Python
predicate (for the live stream). Sharing one AST across both targets is the
design payoff of building a real little language rather than ad-hoc string
matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Tuple

from logscope.model import Level, LogEvent

# A SQL fragment plus its bound parameters, e.g. ("level >= ?", [4]).
SqlFragment = Tuple[str, list]
Predicate = Callable[[LogEvent], bool]


class Term:
    """Base class for a single query term."""

    def to_sql(self) -> SqlFragment:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_predicate(self) -> Predicate:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass(frozen=True)
class LevelTerm(Term):
    """``level:error`` -> severity >= ERROR."""

    level: Level

    def to_sql(self) -> SqlFragment:
        return ("level >= ?", [int(self.level)])

    def to_predicate(self) -> Predicate:
        threshold = self.level
        return lambda ev: ev.level >= threshold


@dataclass(frozen=True)
class SourceTerm(Term):
    """``source:api`` -> exact source match."""

    source: str

    def to_sql(self) -> SqlFragment:
        return ("source = ?", [self.source])

    def to_predicate(self) -> Predicate:
        want = self.source
        return lambda ev: ev.source == want


@dataclass(frozen=True)
class FieldTerm(Term):
    """``request_id:a1b2`` -> match an extracted field's value.

    History stores fields as a JSON blob; we match with a LIKE on the serialized
    key/value, which is good enough for a single-node tool and avoids a join.
    """

    key: str
    value: str

    def to_sql(self) -> SqlFragment:
        # Match the JSON-encoded "key": "value" (and unquoted numeric) loosely.
        like = f'%"{self.key}":%{self.value}%'
        return ("fields LIKE ?", [like])

    def to_predicate(self) -> Predicate:
        key, value = self.key, self.value
        return lambda ev: str(ev.fields.get(key, "")) == value


@dataclass(frozen=True)
class TimeTerm(Term):
    """``last:5m`` -> ts within the last N (resolved at evaluation time)."""

    seconds: int

    def _cutoff_ms(self) -> int:
        now = datetime.now(timezone.utc)
        return int(now.timestamp() * 1000) - self.seconds * 1000

    def to_sql(self) -> SqlFragment:
        return ("ts >= ?", [self._cutoff_ms()])

    def to_predicate(self) -> Predicate:
        cutoff_ms = self._cutoff_ms()
        return lambda ev: int(ev.timestamp.timestamp() * 1000) >= cutoff_ms


@dataclass(frozen=True)
class TextTerm(Term):
    """Free text -> FTS MATCH in history, substring on the live stream."""

    text: str

    def to_sql(self) -> SqlFragment:
        # Handled specially by the store (compiled to an FTS MATCH subquery),
        # but we still expose a fallback LIKE for stores without FTS.
        return ("message LIKE ?", [f"%{self.text}%"])

    def to_predicate(self) -> Predicate:
        needle = self.text.lower()
        return lambda ev: needle in ev.message.lower()


@dataclass(frozen=True)
class Query:
    """A conjunction of terms. Empty query matches everything."""

    terms: List[Term] = field(default_factory=list)

    def text_terms(self) -> List[TextTerm]:
        return [t for t in self.terms if isinstance(t, TextTerm)]

    def non_text_terms(self) -> List[Term]:
        return [t for t in self.terms if not isinstance(t, TextTerm)]

    def to_predicate(self) -> Predicate:
        """Compile to a single AND-ed predicate for the live stream."""
        predicates = [t.to_predicate() for t in self.terms]
        return lambda ev: all(p(ev) for p in predicates)
