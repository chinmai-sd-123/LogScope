"""Tokenizer for the query language.

Turns a query string into a flat list of tokens. Keeping lexing separate from
parsing is the standard compiler-front-end split: the lexer worries about
characters, the parser worries about grammar.

Grammar (see handbook section 16):

    query      := term (WS term)*
    term       := field_term | level_term | time_term | free_text
    field_term := IDENT ":" value
    level_term := "level" ":" level
    time_term  := "last" ":" duration
    free_text  := STRING | WORD
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List


class TokenType(Enum):
    IDENT = auto()    # a bare word: source, level, last, or a free-text word
    STRING = auto()   # a quoted phrase: "connection timeout"
    COLON = auto()    # the ':' separating field and value
    EOF = auto()


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    pos: int  # column in the source, for error messages


class LexError(ValueError):
    """Raised on an unterminated string or other lexical fault."""


def tokenize(text: str) -> List[Token]:
    """Lex ``text`` into tokens, ending with an EOF token."""
    tokens: List[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]

        if ch.isspace():
            i += 1
            continue

        if ch == ":":
            tokens.append(Token(TokenType.COLON, ":", i))
            i += 1
            continue

        if ch == '"':
            start = i
            i += 1
            buf = []
            while i < n and text[i] != '"':
                # allow \" escape inside a quoted string
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                buf.append(text[i])
                i += 1
            if i >= n:
                raise LexError(f"unterminated string starting at column {start}")
            i += 1  # consume closing quote
            tokens.append(Token(TokenType.STRING, "".join(buf), start))
            continue

        # A bare word runs until whitespace, ':' or '"'.
        start = i
        while i < n and not text[i].isspace() and text[i] not in ':"':
            i += 1
        tokens.append(Token(TokenType.IDENT, text[start:i], start))

    tokens.append(Token(TokenType.EOF, "", n))
    return tokens
