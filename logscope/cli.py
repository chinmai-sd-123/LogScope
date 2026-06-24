"""The ``logscope`` command-line entrypoint.

Subcommands grow per phase:
  * ``tail``  -- Phase 1: live TUI over one or more files.
  * ``search`` -- Phase 2 (planned).
  * ``serve`` / ``agent`` -- Phase 4 (planned).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import typer

app = typer.Typer(
    add_completion=False,
    help="A terminal-native log-intelligence and incident-triage tool.",
)


@app.command()
def tail(
    paths: List[Path] = typer.Argument(..., help="Log file(s) to tail."),
    from_start: bool = typer.Option(
        False, "--from-start", help="Read existing contents first, then follow."
    ),
) -> None:
    """Live-tail one or more files in a filterable TUI."""
    from logscope.ingest.source import FileSource
    from logscope.tui.app import LogScopeApp

    sources = [FileSource(p, from_start=from_start) for p in paths]
    LogScopeApp(sources).run()


def main() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
