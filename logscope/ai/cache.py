"""A semantic cache keyed on a cluster's template fingerprint.

Identical clusters never trigger a second provider call. Keying on the *template*
fingerprint (the generalized line plus salient field keys) rather than raw lines
is what makes the cache actually hit -- two incidents with the same shape share
an entry even though their concrete IDs differ.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional


class SummaryCache:
    """A small bounded LRU cache of fingerprint -> summary."""

    def __init__(self, max_entries: int = 256) -> None:
        self.max_entries = max_entries
        self._data: "OrderedDict[str, str]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[str]:
        if key in self._data:
            self._data.move_to_end(key)  # mark as recently used
            self.hits += 1
            return self._data[key]
        self.misses += 1
        return None

    def set(self, key: str, value: str) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)  # evict least-recently-used

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def __len__(self) -> int:
        return len(self._data)
