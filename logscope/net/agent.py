"""The agent: tail local logs and ship batched events to the server.

Handles the failure paths:

  - Batching: flush every N events or T seconds, whichever comes first.
  - Reconnect with exponential backoff + jitter (capped) to avoid a thundering
    herd when a downed server recovers.
  - Bounded local spool while disconnected; drop-oldest when full.
  - At-least-once: resend a batch until acked. Duplicates from a lost ack are
    de-duped server-side by stable event id.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from itertools import islice
from typing import Deque, List, Optional

from logscope.ingest.source import Source
from logscope.model import LogEvent
from logscope.net.protocol import encode_batch, write_frame

log = logging.getLogger("logscope.agent")


class Agent:
    def __init__(
        self,
        agent_id: str,
        sources: List[Source],
        server_host: str,
        server_port: int,
        *,
        batch_size: int = 100,
        flush_interval: float = 1.0,
        spool_max: int = 50_000,
        backoff_cap: float = 30.0,
    ) -> None:
        self.agent_id = agent_id
        self.sources = sources
        self.server_host = server_host
        self.server_port = server_port
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.backoff_cap = backoff_cap

        # Bounded local spool: events captured but not yet acked by the server.
        self.spool: Deque[LogEvent] = deque(maxlen=spool_max)
        self.dropped = 0
        self._stop = asyncio.Event()

    def _enqueue(self, event: LogEvent) -> None:
        if len(self.spool) == self.spool.maxlen:
            self.dropped += 1  # deque(maxlen) drops the oldest on append
        self.spool.append(event)

    async def _collect(self, source: Source) -> None:
        async for event in source.events(stop=self._stop):
            self._enqueue(event)

    async def _connect(self):
        """Open a connection, retrying with exponential backoff + jitter."""
        delay = 0.5
        while not self._stop.is_set():
            try:
                reader, writer = await asyncio.open_connection(
                    self.server_host, self.server_port
                )
                log.info("connected to %s:%s", self.server_host, self.server_port)
                return reader, writer
            except (ConnectionError, OSError) as exc:
                jitter = random.uniform(0, delay * 0.5)
                wait = min(delay + jitter, self.backoff_cap)
                log.warning("connect failed (%s); retrying in %.1fs", exc, wait)
                await asyncio.sleep(wait)
                delay = min(delay * 2, self.backoff_cap)
        return None

    async def _ship(self) -> None:
        """Drain the spool to the server, reconnecting and resending as needed."""
        reader = writer = None
        while not self._stop.is_set():
            if writer is None:
                conn = await self._connect()
                if conn is None:
                    return
                reader, writer = conn

            if not self.spool:
                await asyncio.sleep(self.flush_interval)
                continue

            # Keep the batch in the spool until the server acks (at-least-once).
            batch = list(islice(self.spool, self.batch_size))
            try:
                await write_frame(writer, encode_batch(self.agent_id, batch))
                ack = await reader.readexactly(1)  # wait for the server ACK
                if ack != b"\x06":
                    raise ConnectionError("unexpected ack")
            except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
                log.warning("send failed (%s); will reconnect and resend", exc)
                if writer is not None:
                    writer.close()
                reader = writer = None
                continue  # batch stays in the spool -> resend after reconnect

            # Acked: now it's safe to drop these from the spool.
            for _ in range(len(batch)):
                if self.spool:
                    self.spool.popleft()

    async def run(self) -> None:
        collectors = [asyncio.create_task(self._collect(s)) for s in self.sources]
        shipper = asyncio.create_task(self._ship())
        try:
            await asyncio.gather(*collectors, shipper)
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        self._stop.set()


async def run_agent(
    agent_id: str, paths: list, server_host: str, server_port: int, from_start: bool
) -> None:
    from logscope.ingest.source import FileSource

    sources = [FileSource(p, from_start=from_start) for p in paths]
    agent = Agent(agent_id, sources, server_host, server_port)
    await agent.run()
