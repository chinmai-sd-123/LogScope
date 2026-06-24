"""The central server: accept event batches from agents and persist them.

Ingestion is idempotent -- the store keys on a stable ``event_id`` and uses
``INSERT OR IGNORE`` -- so a duplicate batch (an agent resend after a lost ack)
has no extra effect. Combined with the agent's at-least-once delivery, this gives
effectively-once semantics without the cost of true exactly-once.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from logscope.index.store import EventStore
from logscope.net.protocol import decode_batch, read_frame

log = logging.getLogger("logscope.server")


class Server:
    def __init__(self, store: EventStore, host: str = "0.0.0.0", port: int = 9099) -> None:
        self.store = store
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None
        self._clients: set[asyncio.StreamWriter] = set()
        self.events_received = 0

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log.info("agent connected: %s", peer)
        self._clients.add(writer)
        try:
            while True:
                try:
                    payload = await read_frame(reader)
                except asyncio.IncompleteReadError:
                    break  # clean disconnect
                try:
                    agent_id, events = decode_batch(payload)
                except ValueError as exc:
                    log.warning("bad batch from %s: %s", peer, exc)
                    continue  # skip a malformed/old-version batch, keep the conn
                self.store.add_many(events)
                self.store.flush()  # durable before we ack
                self.events_received += len(events)
                writer.write(b"\x06")  # one-byte ACK
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            log.info("agent disconnected: %s", peer)
            self._clients.discard(writer)
            writer.close()

    async def serve_forever(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        log.info("listening on %s", addrs)
        async with self._server:
            await self._server.serve_forever()

    async def start(self) -> int:
        """Start serving in the background; return the bound port (for tests)."""
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        asyncio.create_task(self._server.serve_forever())
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop accepting and forcibly drop active connections (simulates a crash)."""
        if self._server is None:
            return
        self._server.close()
        # Abort live client connections so agents see a disconnect immediately,
        # rather than waiting on them in wait_closed() (which blocks since 3.12).
        for writer in list(self._clients):
            writer.close()
        self._clients.clear()
        try:
            await asyncio.wait_for(self._server.wait_closed(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


async def run_server(db: Path | str, host: str, port: int) -> None:
    store = EventStore(db)
    server = Server(store, host=host, port=port)
    try:
        await server.serve_forever()
    finally:
        store.close()
