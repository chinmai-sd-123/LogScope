"""Self-instrumentation: a tiny in-process metrics registry.

A tool that watches logs should watch itself. Nothing here depends on a metrics
backend -- counters, gauges, a rolling latency histogram, and a sliding-window
rate meter, all stdlib-only. The TUI shows a one-line summary; the store records
query latency.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Deque, Optional, Tuple


class Counter:
    """A monotonically increasing total."""

    def __init__(self) -> None:
        self._v = 0

    def inc(self, n: int = 1) -> None:
        self._v += n

    @property
    def value(self) -> int:
        return self._v


class Gauge:
    """A value that goes up and down (queue depth, lag, ...)."""

    def __init__(self) -> None:
        self._v = 0.0

    def set(self, v: float) -> None:
        self._v = float(v)

    @property
    def value(self) -> float:
        return self._v


class Histogram:
    """A bounded sample of observations supporting percentile queries."""

    def __init__(self, maxlen: int = 1024) -> None:
        self._samples: Deque[float] = deque(maxlen=maxlen)

    def observe(self, v: float) -> None:
        self._samples.append(float(v))

    def percentile(self, p: float) -> float:
        """Linear-interpolated percentile (p in [0, 100]). 0.0 if empty."""
        if not self._samples:
            return 0.0
        ordered = sorted(self._samples)
        if len(ordered) == 1:
            return ordered[0]
        k = (len(ordered) - 1) * (p / 100.0)
        lo = int(k)
        hi = min(lo + 1, len(ordered) - 1)
        if lo == hi:
            return ordered[lo]
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)

    @property
    def count(self) -> int:
        return len(self._samples)


class RateMeter:
    """Events per second over a sliding window, bucketed by whole seconds."""

    def __init__(self, window_s: int = 5) -> None:
        self.window_s = window_s
        self._buckets: Deque[Tuple[int, int]] = deque()  # (second, count)

    def _now(self, now: Optional[float]) -> int:
        return int(now if now is not None else time.monotonic())

    def mark(self, n: int = 1, now: Optional[float] = None) -> None:
        sec = self._now(now)
        if self._buckets and self._buckets[-1][0] == sec:
            s, c = self._buckets[-1]
            self._buckets[-1] = (s, c + n)
        else:
            self._buckets.append((sec, n))
        self._evict(sec)

    def _evict(self, sec: int) -> None:
        while self._buckets and self._buckets[0][0] <= sec - self.window_s:
            self._buckets.popleft()

    def rate(self, now: Optional[float] = None) -> float:
        sec = self._now(now)
        self._evict(sec)
        total = sum(c for _, c in self._buckets)
        return total / self.window_s


class Metrics:
    """The live registry shown in the TUI status bar."""

    def __init__(self) -> None:
        self.events_total = Counter()
        self.ingest_rate = RateMeter()
        self.queue_depth = Gauge()
        self.ingest_lag_ms = Gauge()
        self.cluster_count = Gauge()
        self.query_latency_ms = Histogram()

    def record_event(self, lag_ms: float, now: Optional[float] = None) -> None:
        self.events_total.inc()
        self.ingest_rate.mark(now=now)
        self.ingest_lag_ms.set(lag_ms)

    def snapshot(self) -> dict:
        return {
            "events_total": self.events_total.value,
            "ingest_per_s": round(self.ingest_rate.rate(), 1),
            "queue_depth": int(self.queue_depth.value),
            "ingest_lag_ms": int(self.ingest_lag_ms.value),
            "clusters": int(self.cluster_count.value),
            "query_p50_ms": round(self.query_latency_ms.percentile(50), 1),
            "query_p95_ms": round(self.query_latency_ms.percentile(95), 1),
        }

    def status_line(self, ai_hit_rate: Optional[float] = None) -> str:
        s = self.snapshot()
        parts = [
            f"in {s['ingest_per_s']}/s",
            f"q {s['queue_depth']}",
            f"lag {s['ingest_lag_ms']}ms",
            f"clusters {s['clusters']}",
            f"total {s['events_total']}",
        ]
        if ai_hit_rate is not None:
            parts.append(f"ai-cache {int(ai_hit_rate * 100)}%")
        return "  |  ".join(parts)
