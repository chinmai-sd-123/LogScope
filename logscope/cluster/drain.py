"""Drain: an online log-template miner.

Collapses near-identical log lines (differing only in ids/numbers/timestamps)
into templates with a count. A fixed-depth parse tree keeps the work per line
roughly constant regardless of how many templates exist:

    root -> length group -> first N tokens -> leaf (candidate templates)

At the leaf, the message is compared to each candidate by the fraction of
matching token positions (<*> is a wildcard). Above sim_threshold it merges
(differing positions become <*>); otherwise a new template is created.

Tuning: depth (number of prefix layers), sim_threshold (match strictness),
max_children (fan-out cap; overflow collapses into a shared <*> branch).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

WILDCARD = "<*>"

# Mask obvious variables before tokenizing so they never fragment templates.
# Order matters: more specific patterns first.
_MASKS = (
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), WILDCARD),  # UUID
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), WILDCARD),       # IPv4
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), WILDCARD),                # hex
    (re.compile(r"\b\d+\b"), WILDCARD),                           # integers
)


def _preprocess(message: str) -> List[str]:
    """Mask variables, then split into tokens on whitespace."""
    masked = message
    for pattern, repl in _MASKS:
        masked = pattern.sub(repl, masked)
    return masked.split()


@dataclass
class Template:
    """A mined template: a token list with ``<*>`` wildcards, plus a count."""

    id: int
    tokens: List[str]
    count: int = 0

    def as_string(self) -> str:
        return " ".join(self.tokens)


@dataclass
class _Node:
    """An internal tree node keyed by token (or ``<*>`` for the overflow branch)."""

    children: Dict[str, "_Node"] = field(default_factory=dict)
    templates: List[Template] = field(default_factory=list)  # only populated at leaves


def seq_distance(template_tokens: List[str], message_tokens: List[str]) -> float:
    """Fraction of positions where tokens match (``<*>`` is a wildcard)."""
    if len(template_tokens) != len(message_tokens):
        return 0.0
    if not template_tokens:
        return 1.0
    matches = sum(
        1
        for t, m in zip(template_tokens, message_tokens)
        if t == WILDCARD or t == m
    )
    return matches / len(template_tokens)


def merge_tokens(template_tokens: List[str], message_tokens: List[str]) -> List[str]:
    """Generalize a template: positions that now differ become ``<*>``.

    A template only ever *gains* wildcards (generalizes); it never re-specializes.
    """
    return [
        t if t == m else WILDCARD
        for t, m in zip(template_tokens, message_tokens)
    ]


class Drain:
    """Online Drain template miner."""

    def __init__(
        self,
        *,
        depth: int = 4,
        sim_threshold: float = 0.4,
        max_children: int = 100,
    ) -> None:
        # Effective token-prefix depth (excludes the root and length layers).
        self.depth = max(2, depth) - 2
        self.sim_threshold = sim_threshold
        self.max_children = max_children
        self._root = _Node()
        self._templates: List[Template] = []
        self._next_id = 0

    @property
    def templates(self) -> List[Template]:
        """All mined templates, most frequent first."""
        return sorted(self._templates, key=lambda t: t.count, reverse=True)

    def add_message(self, message: str) -> Template:
        """Process one message; return the template it matched or created."""
        tokens = _preprocess(message)
        leaf = self._descend(tokens, create=True)
        template = self._match_or_create(leaf, tokens)
        template.count += 1
        return template

    # -- tree navigation --------------------------------------------------- #

    def _descend(self, tokens: List[str], *, create: bool) -> _Node:
        """Walk root -> length group -> up to `depth` leading tokens -> leaf."""
        # Layer 1: group by token count.
        length_key = str(len(tokens))
        node = self._child(self._root, length_key, create)

        # Layers 2..depth+1: branch on leading tokens.
        for token in tokens[: self.depth]:
            # A token that is already a masked variable routes to the wildcard branch.
            key = WILDCARD if token == WILDCARD else token
            if key not in node.children and len(node.children) >= self.max_children:
                key = WILDCARD  # overflow: collapse into the shared wildcard branch
            node = self._child(node, key, create)
        return node

    @staticmethod
    def _child(node: _Node, key: str, create: bool) -> _Node:
        child = node.children.get(key)
        if child is None and create:
            child = _Node()
            node.children[key] = child
        return child if child is not None else node

    # -- leaf matching ----------------------------------------------------- #

    def _match_or_create(self, leaf: _Node, tokens: List[str]) -> Template:
        best: Optional[Template] = None
        best_sim = -1.0
        for candidate in leaf.templates:
            sim = seq_distance(candidate.tokens, tokens)
            if sim > best_sim:
                best_sim, best = sim, candidate

        if best is not None and best_sim >= self.sim_threshold:
            best.tokens = merge_tokens(best.tokens, tokens)  # generalize in place
            return best

        template = Template(id=self._next_id, tokens=list(tokens))
        self._next_id += 1
        self._templates.append(template)
        leaf.templates.append(template)
        return template
