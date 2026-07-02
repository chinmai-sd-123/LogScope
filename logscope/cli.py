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


@app.callback()
def _bootstrap() -> None:
    """Runs before any subcommand: load .env into the environment."""
    from logscope.config import load_env

    load_env()


def _resolve_db(db: Optional[Path]) -> Path:
    """Resolve the SQLite path: --db flag, else LOGSCOPE_DB, else ./logscope.db.

    Returned absolute so commands run from different directories don't silently
    talk to different databases.
    """
    import os

    if db is None:
        db = Path(os.environ.get("LOGSCOPE_DB", "logscope.db"))
    return db.resolve()


_DB_HELP = "SQLite file for events (default: $LOGSCOPE_DB or ./logscope.db)."


@app.command()
def tail(
    paths: List[Path] = typer.Argument(..., help="Log file(s) to tail."),
    from_start: bool = typer.Option(
        False, "--from-start", help="Read existing contents first, then follow."
    ),
    db: Optional[Path] = typer.Option(None, "--db", help=_DB_HELP),
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
    store = None
    if not no_store:
        db = _resolve_db(db)
        typer.echo(f"db: {db}")
        store = EventStore(db)
    # Self-disables when OPENAI_API_KEY is absent; the TUI degrades gracefully.
    summarizer = OpenAISummarizer()
    LogScopeApp(sources, store=store, summarizer=summarizer).run()


@app.command()
def search(
    query: str = typer.Argument(..., help='Query, e.g. \'level:error last:1h "timeout"\'.'),
    db: Optional[Path] = typer.Option(None, "--db", help=_DB_HELP),
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

    db = _resolve_db(db)
    console.print(f"[dim]db: {db}[/]")
    if not db.exists():
        console.print(f"[yellow]no index at {db}; run 'logscope tail' first.[/]")
        raise typer.Exit(code=1)

    with EventStore(db) as store:
        results = store.search(parsed, limit=limit)
        took_ms = store.query_latency_ms.percentile(100)  # this query's latency

    if not results:
        console.print(f"[dim]no matches. ({took_ms:.1f} ms)[/]")
        return
    for event in reversed(results):  # print oldest-first so newest is at the bottom
        console.print(render_event(event))
    console.print(f"[dim]{len(results)} match(es) in {took_ms:.1f} ms.[/]")


@app.command()
def serve(
    db: Optional[Path] = typer.Option(None, "--db", help=_DB_HELP),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address."),
    port: int = typer.Option(9099, "--port", help="Listen port."),
) -> None:
    """Run the central server that accepts events from agents."""
    import asyncio
    import logging

    from logscope.net.server import run_server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    db = _resolve_db(db)
    logging.getLogger("logscope.server").info("using db: %s", db)
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
