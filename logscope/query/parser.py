"""Parse a token stream into a :class:`Query` AST.

A hand-written recursive-descent-style parser over a flat token list. The
grammar is AND-only and regular, so this stays small -- a deliberate choice to
ship a correct little language rather than a buggy big one (OR/NOT/parens are a
documented stretch goal).
"""

from __future__ import annotations

import re

from logscope.parse.parser import normalize_level
from logscope.query.ast import (
    FieldTerm,
    LevelTerm,
    Query,
    SourceTerm,
    TextTerm,
    TimeTerm,
)
from logscope.query.lexer import Token, TokenType, tokenize

_DURATION = re.compile(r"^(\d+)([smhd])$")
_DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class ParseError(ValueError):
    """Raised when a query is syntactically or semantically invalid."""


def _parse_duration(value: str, pos: int) -> int:
    m = _DURATION.match(value)
    if not m:
        raise ParseError(
            f"invalid duration '{value}' at column {pos} (expected e.g. 5m, 2h, 1d)"
        )
    return int(m.group(1)) * _DURATION_UNIT_SECONDS[m.group(2)]


def parse_query(text: str) -> Query:
    """Parse a query string into a :class:`Query`. Empty string -> match all."""
    tokens = tokenize(text)
    terms = []
    i = 0

    while tokens[i].type != TokenType.EOF:
        tok = tokens[i]

        # A quoted string is always free text.
        if tok.type == TokenType.STRING:
            terms.append(TextTerm(tok.value))
            i += 1
            continue

        if tok.type == TokenType.IDENT:
            # Look ahead for "ident : value" (a field term), else free text.
            if tokens[i + 1].type == TokenType.COLON:
                key = tok.value
                value_tok = tokens[i + 2]
                if value_tok.type not in (TokenType.IDENT, TokenType.STRING):
                    raise ParseError(
                        f"expected a value after '{key}:' at column {tok.pos}"
                    )
                terms.append(_field_term(key, value_tok))
                i += 3
                continue
            # Bare word -> free text.
            terms.append(TextTerm(tok.value))
            i += 1
            continue

        raise ParseError(f"unexpected token '{tok.value}' at column {tok.pos}")

    return Query(terms)


def _field_term(key: str, value_tok: Token):
    """Build the right Term subclass for a ``key:value`` pair."""
    value = value_tok.value
    key_lower = key.lower()

    if key_lower == "level":
        level = normalize_level(value)
        if level is None:
            raise ParseError(
                f"unknown level '{value}' at column {value_tok.pos}"
            )
        return LevelTerm(level)

    if key_lower == "last":
        return TimeTerm(_parse_duration(value, value_tok.pos))

    if key_lower == "source":
        return SourceTerm(value)

    # Anything else is a custom extracted field.
    return FieldTerm(key, value)
