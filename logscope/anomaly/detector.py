"""Sliding-window spike detection.

Deliberately transparent statistics, not a black-box model: an on-call engineer
at 3 a.m. needs "this bucket is 4 standard deviations above the last five
minutes" (actionable) rather than "anomaly score 0.87" (not). That explainability
is the design choice worth defending.

Method:
  * Bucket events into fixed time windows (e.g. 10s).
  * Keep a rolling baseline of the last N completed buckets' counts.
  * Flag a bucket whose count exceeds ``mean + k * stddev`` (z-score), with an
    absolute floor so we don't fire on tiny numbers (3 vs a baseline of ~0).

The window is maintained *incrementally* -- running sum and sum-of-squares
updated on push/pop -- so each tick is O(1) rather than O(N).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass(frozen=True)
class Bucket:
    """One completed time bucket and whether it was flagged."""

    start_ms: int
    count: int
    mean: float
    stddev: float
    z_score: float
    is_anomaly: bool


class AnomalyDetector:
    """Rolling z-score spike detector for a single counted series."""

    def __init__(
        self,
        *,
        bucket_seconds: int = 10,
        window: int = 30,
        k: float = 3.0,
        min_count: int = 5,
    ) -> None:
        self.bucket_ms = bucket_seconds * 1000
        self.window = window
        self.k = k
        self.min_count = min_count  # absolute floor to avoid firing on noise

        self._counts: Deque[int] = deque(maxlen=window)
        self._sum = 0.0
        self._sum_sq = 0.0

        self._current_bucket: Optional[int] = None  # bucket start (ms) being filled
        self._current_count = 0

    def _bucket_start(self, ts_ms: int) -> int:
        return (ts_ms // self.bucket_ms) * self.bucket_ms

    def add(self, ts_ms: int) -> Optional[Bucket]:
        """Record an event at ``ts_ms``. Returns a completed :class:`Bucket` when
        crossing into a new time bucket, else ``None``."""
        bucket = self._bucket_start(ts_ms)

        if self._current_bucket is None:
            self._current_bucket = bucket
            self._current_count = 1
            return None

        if bucket == self._current_bucket:
            self._current_count += 1
            return None

        # Crossed into a new bucket: finalize the one we were filling.
        completed = self._finalize(self._current_bucket, self._current_count)
        # Account for any empty buckets skipped between the two timestamps so a
        # gap of silence correctly lowers the baseline.
        gap = (bucket - self._current_bucket) // self.bucket_ms
        for i in range(1, gap):
            self._roll(0)  # empty buckets contribute zero to the baseline
        self._current_bucket = bucket
        self._current_count = 1
        return completed

    def _finalize(self, start_ms: int, count: int) -> Bucket:
        """Score ``count`` against the current baseline, then roll it into it."""
        mean, stddev = self._stats()
        if len(self._counts) < 2:
            z = 0.0
            is_anomaly = False  # not enough history to judge
        else:
            z = (count - mean) / stddev if stddev > 0 else (
                math.inf if count > mean else 0.0
            )
            is_anomaly = count >= self.min_count and z >= self.k
        self._roll(count)
        return Bucket(start_ms, count, mean, stddev, z, is_anomaly)

    def _stats(self) -> tuple[float, float]:
        n = len(self._counts)
        if n == 0:
            return 0.0, 0.0
        mean = self._sum / n
        variance = max(0.0, self._sum_sq / n - mean * mean)
        return mean, math.sqrt(variance)

    def _roll(self, count: int) -> None:
        """Push ``count`` into the rolling window, updating sums incrementally."""
        if len(self._counts) == self.window:
            evicted = self._counts[0]  # deque(maxlen) will drop this on append
            self._sum -= evicted
            self._sum_sq -= evicted * evicted
        self._counts.append(count)
        self._sum += count
        self._sum_sq += count * count

    def recent_counts(self) -> list[int]:
        """The rolling window's counts (oldest first) -- for the sparkline."""
        return list(self._counts)
