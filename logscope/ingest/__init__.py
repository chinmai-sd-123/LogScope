"""Ingest: getting raw lines off of sources and into the pipeline."""

from logscope.ingest.source import FileSource, Source
from logscope.ingest.tailer import tail

__all__ = ["tail", "Source", "FileSource"]
