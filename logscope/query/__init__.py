"""A small query language: lexer -> parser -> AST -> (SQL | live predicate).

The same AST compiles to two evaluation targets -- a parameterized SQL WHERE
clause for searching history, and a Python predicate for filtering the live
stream -- so one grammar serves both. See docs/decisions.md D4.
"""

from logscope.query.ast import Query
from logscope.query.parser import ParseError, parse_query

__all__ = ["Query", "parse_query", "ParseError"]
