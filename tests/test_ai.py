import asyncio

import pytest

from logscope.ai.cache import SummaryCache
from logscope.ai.summarizer import (
    ClusterContext,
    NullSummarizer,
    summarize_cluster,
)


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def test_cache_hit_and_miss_counts():
    cache = SummaryCache()
    assert cache.get("k") is None        # miss
    cache.set("k", "summary")
    assert cache.get("k") == "summary"   # hit
    assert cache.hits == 1 and cache.misses == 1
    assert cache.hit_rate == 0.5


def test_cache_lru_eviction():
    cache = SummaryCache(max_entries=2)
    cache.set("a", "1")
    cache.set("b", "2")
    cache.get("a")           # touch 'a' so 'b' becomes least-recent
    cache.set("c", "3")      # evicts 'b'
    assert cache.get("a") == "1"
    assert cache.get("c") == "3"
    assert cache.get("b") is None


# --------------------------------------------------------------------------- #
# Fingerprint
# --------------------------------------------------------------------------- #


def test_fingerprint_stable_for_same_template():
    a = ClusterContext("db <*> failed", 100).fingerprint()
    b = ClusterContext("db <*> failed", 150).fingerprint()  # same magnitude bucket
    c = ClusterContext("disk full", 100).fingerprint()
    assert a == b           # same template + magnitude -> same key
    assert a != c           # different template -> different key


# --------------------------------------------------------------------------- #
# Graceful degradation (the whole point of this phase)
# --------------------------------------------------------------------------- #


class _FakeSummarizer:
    def __init__(self, *, enabled=True, behavior="ok", calls=None):
        self._enabled = enabled
        self._behavior = behavior
        self.calls = calls if calls is not None else []

    @property
    def enabled(self):
        return self._enabled

    async def summarize(self, prompt):
        self.calls.append(prompt)
        if self._behavior == "hang":
            await asyncio.sleep(10)
        if self._behavior == "boom":
            raise RuntimeError("provider exploded")
        return "hypothesis: the database is unreachable; check connectivity"


@pytest.mark.asyncio
async def test_disabled_provider_returns_none():
    ctx = ClusterContext("x", 1)
    out = await summarize_cluster(ctx, NullSummarizer(), SummaryCache())
    assert out is None


@pytest.mark.asyncio
async def test_successful_summary_is_cached():
    ctx = ClusterContext("db <*> failed", 100, ["db 7 failed", "db 3 failed"])
    cache = SummaryCache()
    fake = _FakeSummarizer()
    first = await summarize_cluster(ctx, fake, cache)
    assert "database" in first
    # A second identical cluster must hit the cache (no second provider call).
    second = await summarize_cluster(ctx, fake, cache)
    assert second == first
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_timeout_degrades_to_none():
    ctx = ClusterContext("x", 1)
    out = await summarize_cluster(ctx, _FakeSummarizer(behavior="hang"),
                                  SummaryCache(), timeout=0.05)
    assert out is None


@pytest.mark.asyncio
async def test_provider_exception_degrades_to_none():
    ctx = ClusterContext("x", 1)
    out = await summarize_cluster(ctx, _FakeSummarizer(behavior="boom"), SummaryCache())
    assert out is None
