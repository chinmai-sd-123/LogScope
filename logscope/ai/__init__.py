"""Optional, additive AI enrichment.

The whole tool works without this package. AI only synthesizes a plain-English
root-cause *hypothesis* for a *selected* cluster, on demand -- never on every
event. It is cached by template fingerprint, bounded by a timeout, and degrades
gracefully: if the provider is down/slow/unconfigured the tool is fully
functional and the summary is simply unavailable.
"""

from logscope.ai.cache import SummaryCache
from logscope.ai.summarizer import (
    ClusterContext,
    NullSummarizer,
    OpenAISummarizer,
    Summarizer,
    summarize_cluster,
)

__all__ = [
    "SummaryCache",
    "ClusterContext",
    "Summarizer",
    "NullSummarizer",
    "OpenAISummarizer",
    "summarize_cluster",
]
