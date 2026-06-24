"""Tailer tests.

These are async and touch the filesystem, so they're slower than the pure-logic
tests -- but rotation/truncation handling is exactly the behavior that separates
a real tailer from a toy, so it's worth covering.
"""

import asyncio

import pytest

from logscope.ingest.tailer import tail


async def _collect(path, *, n, from_start, timeout=3.0):
    """Collect up to ``n`` lines from the tailer, then stop."""
    stop = asyncio.Event()
    out: list[str] = []

    async def run():
        async for line in tail(path, from_start=from_start, poll_interval=0.01, stop=stop):
            out.append(line)
            if len(out) >= n:
                stop.set()
                return

    try:
        await asyncio.wait_for(run(), timeout=timeout)
    except asyncio.TimeoutError:
        stop.set()
    return out


@pytest.mark.asyncio
async def test_reads_existing_lines_from_start(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    lines = await _collect(f, n=3, from_start=True)
    assert lines == ["line1", "line2", "line3"]


@pytest.mark.asyncio
async def test_tails_appended_lines(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("", encoding="utf-8")

    stop = asyncio.Event()
    out: list[str] = []

    async def reader():
        async for line in tail(f, from_start=True, poll_interval=0.01, stop=stop):
            out.append(line)
            if len(out) >= 2:
                stop.set()
                return

    async def writer():
        await asyncio.sleep(0.05)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("hello\n")
            fh.flush()
            await asyncio.sleep(0.05)
            fh.write("world\n")
            fh.flush()

    await asyncio.wait_for(asyncio.gather(reader(), writer()), timeout=3.0)
    assert out == ["hello", "world"]


@pytest.mark.asyncio
async def test_does_not_emit_partial_lines(tmp_path):
    f = tmp_path / "app.log"
    f.write_text("", encoding="utf-8")

    stop = asyncio.Event()
    out: list[str] = []

    async def reader():
        async for line in tail(f, from_start=True, poll_interval=0.01, stop=stop):
            out.append(line)
            if out:
                stop.set()
                return

    async def writer():
        await asyncio.sleep(0.05)
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("half ")     # no newline yet
            fh.flush()
            await asyncio.sleep(0.08)
            fh.write("complete\n")  # now the line is whole
            fh.flush()

    await asyncio.wait_for(asyncio.gather(reader(), writer()), timeout=3.0)
    assert out == ["half complete"]  # never the partial "half "


@pytest.mark.asyncio
async def test_survives_truncation(tmp_path):
    """Truncating the file (size shrinks below offset) triggers a reopen."""
    f = tmp_path / "app.log"
    f.write_text("old1\nold2\n", encoding="utf-8")

    stop = asyncio.Event()
    out: list[str] = []

    async def reader():
        async for line in tail(f, from_start=True, poll_interval=0.01, stop=stop):
            out.append(line)
            if "fresh" in line:
                stop.set()
                return

    async def rotator():
        await asyncio.sleep(0.08)
        # Truncate and write a fresh, shorter set of contents.
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("fresh\n")
            fh.flush()

    await asyncio.wait_for(asyncio.gather(reader(), rotator()), timeout=3.0)
    assert "old1" in out and "old2" in out
    assert "fresh" in out  # the post-truncation line was picked up
