"""Minimal .env loading, no third-party dependency.

Reads KEY=VALUE lines from a .env file into os.environ without overriding values
already set in the real environment. Called once at CLI startup so OPENAI_API_KEY
and friends can live in a local .env instead of being exported by hand.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path = ".env") -> None:
    """Load KEY=VALUE pairs from ``path`` if it exists. Existing vars win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
