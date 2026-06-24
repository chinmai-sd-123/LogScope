"""Cluster summarization with strict guardrails.

Design rules (the senior signal of this phase):

* **Additive, never required.** A disabled/failed/slow provider yields ``None``;
  the tool keeps working.
* **Cached by fingerprint.** Identical clusters never call twice.
* **Bounded.** Hard per-call timeout; we send a *sample* of representative lines
  plus the template/count/time-range, never thousands of raw lines.
* **Grounded.** We ask for a hypothesis with explicit uncertainty and instruct
  the model to say "insufficient information" rather than invent.

The provider is abstracted behind :class:`Summarizer` so OpenAI can be swapped
for another backend (or a fake, in tests) without touching the orchestration.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable

_SYSTEM_PROMPT = (
    "You are a site-reliability assistant. Given a cluster of similar log lines, "
    "produce a brief root-cause HYPOTHESIS and the first checks an on-call "
    "engineer should make. Be concrete but explicitly flag uncertainty. If the "
    "context is insufficient, say so plainly rather than inventing a cause. "
    "Answer in at most 5 short sentences."
)


@dataclass(frozen=True)
class ClusterContext:
    """The bounded context handed to the model for one cluster."""

    template: str
    count: int
    sample_lines: List[str] = field(default_factory=list)
    time_range: Optional[str] = None

    def fingerprint(self) -> str:
        """Stable cache key: the template plus count magnitude bucket."""
        # Bucket the count by order of magnitude so "the same incident, bigger"
        # still reuses the cached explanation.
        magnitude = len(str(self.count))
        return hashlib.sha1(f"{self.template}|{magnitude}".encode()).hexdigest()

    def to_prompt(self, max_lines: int = 15) -> str:
        lines = "\n".join(self.sample_lines[:max_lines])
        parts = [
            f"Template: {self.template}",
            f"Occurrences: {self.count}",
        ]
        if self.time_range:
            parts.append(f"Time range: {self.time_range}")
        parts.append("Sample lines:\n" + lines)
        return "\n".join(parts)


@runtime_checkable
class Summarizer(Protocol):
    """Anything that can turn a prompt into a summary string."""

    @property
    def enabled(self) -> bool:
        ...

    async def summarize(self, prompt: str) -> str:
        ...


class NullSummarizer:
    """A disabled summarizer: always unavailable. The default."""

    enabled = False

    async def summarize(self, prompt: str) -> str:  # pragma: no cover - never called
        raise RuntimeError("summarizer is disabled")


class OpenAISummarizer:
    """Calls OpenAI's chat completions API via httpx.

    Reads ``OPENAI_API_KEY`` from the environment; if absent, ``enabled`` is
    False and the tool degrades to no-summary. ``OPENAI_MODEL`` overrides the
    model (default a small, cheap one).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = base_url.rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def summarize(self, prompt: str) -> str:
        import httpx  # imported lazily so the core never hard-depends on a call

        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()


async def summarize_cluster(
    context: ClusterContext,
    client: Summarizer,
    cache,
    *,
    timeout: float = 8.0,
) -> Optional[str]:
    """Return a summary for ``context``: from cache, from the provider, or None.

    Never raises -- any provider failure (disabled, timeout, network, bad
    response) degrades to ``None`` so the UI can show "summary unavailable".
    """
    import asyncio

    key = context.fingerprint()
    cached = cache.get(key)
    if cached is not None:
        return cached

    if not client.enabled:
        return None

    try:
        summary = await asyncio.wait_for(
            client.summarize(context.to_prompt()), timeout
        )
    except (asyncio.TimeoutError, Exception):
        return None

    if summary:
        cache.set(key, summary)
    return summary
