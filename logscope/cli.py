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
    from logscope.ai.summarizer import OpenAISummarizer
    from logscope.index.store import EventStore
    from logscope.ingest.source import FileSource
    from logscope.tui.app import LogScopeApp

    sources = [FileSource(p, from_start=from_start) for p in paths]
    store = None if no_store else EventStore(db)
    # Self-disables when OPENAI_API_KEY is absent; the TUI degrades gracefully.
    summarizer = OpenAISummarizer()
    LogScopeApp(sources, store=store, summarizer=summarizer).run()


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


@app.command()
def serve(
    db: Path = typer.Option(DEFAULT_DB, "--db", help="SQLite file to persist into."),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address."),
    port: int = typer.Option(9099, "--port", help="Listen port."),
) -> None:
    """Run the central server that accepts events from agents."""
    import asyncio
    import logging

    from logscope.net.server import run_server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(run_server(db, host, port))


@app.command()
def agent(
    paths: List[Path] = typer.Argument(..., help="Log file(s) to tail and ship."),
    server: str = typer.Option("127.0.0.1:9099", "--server", help="host:port of the server."),
    agent_id: Optional[str] = typer.Option(None, "--id", help="Agent identifier."),
    from_start: bool = typer.Option(False, "--from-start", help="Ship existing contents too."),
) -> None:
    """Run a collector that tails local logs and ships them to a server."""
    import asyncio
    import logging
    import socket

    from logscope.net.agent import run_agent

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    host, _, port = server.partition(":")
    aid = agent_id or socket.gethostname()
    asyncio.run(run_agent(aid, paths, host, int(port or 9099), from_start))


def main() -> None:  # pragma: no cover - thin wrapper
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
