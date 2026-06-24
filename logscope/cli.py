"""The ``logscope`` command-line entrypoint.

Subcommands grow per phase:
  * ``tail``   -- Phase 1: live TUI over one or more files (Phase 2: persists).
  * ``search`` -- Phase 2: query indexed history.
  * ``serve`` / ``agent`` -- Phase 4 (planned).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import typer

app = typer.Typer(
    add_completion=False,
    help="A terminal-native log-intelligence and incident-triage tool.",
)

DEFAULT_DB = Path("logscope.db")


@app.command()
def tail(
    paths: List[Path] = typer.Argument(..., help="Log file(s) to tail."),
    from_start: bool = typer.Option(
        False, "--from-start", help="Read existing contents first, then follow."
    ),
    db: Optional[Path] = typer.Option(
        DEFAULT_DB, "--db", help="SQLite file to persist events to (for search)."
    ),
    no_store: bool = typer.Option(
        False, "--no-store", help="Do not persist events; tail-only."
    ),
) -> None:
    """Live-tail one or more files in a filterable TUI."""
    from logscope.index.store import EventStore
    from logscope.ingest.source import FileSource
    from logscope.tui.app import LogScopeApp

    sources = [FileSource(p, from_start=from_start) for p in paths]
    store = None if no_store else EventStore(db)
    LogScopeApp(sources, store=store).run()


@app.command()
def search(
    query: str = typer.Argument(..., help='Query, e.g. \'level:error last:1h "timeout"\'.'),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite file to search."),
    limit: int = typer.Option(100, "--limit", "-n", help="Max results."),
) -> None:
    """Search indexed history with the query language."""
    from rich.console import Console

    from logscope.index.store import EventStore
    from logscope.query.parser import ParseError, parse_query
    from logscope.tui.widgets import render_event

    console = Console()
    try:
        parsed = parse_query(query)
    except ParseError as exc:
        console.print(f"[bold red]query error:[/] {exc}")
        raise typer.Exit(code=2)

    if not db.exists():
        console.print(f"[yellow]no index at {db}; run 'logscope tail' first.[/]")
        raise typer.Exit(code=1)

    with EventStore(db) as store:
        results = store.search(parsed, limit=limit)

    if not results:
        console.print("[dim]no matches.[/]")
        return
    for event in reversed(results):  # print oldest-first so newest is at the bottom
        console.print(render_event(event))
    console.print(f"[dim]{len(results)} match(es).[/]")


def main() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
