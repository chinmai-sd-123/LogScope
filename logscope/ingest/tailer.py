"""File tailing that survives rotation and truncation.

The naive ``open`` + ``readline`` loop breaks the moment a log rotates: the OS
renames the active file and creates a fresh one at the same path, and the naive
tailer keeps reading the now-orphaned old handle forever. A real tailer detects
rotation and reopens.

Rotation detection here uses two independent signals so it works across
platforms (including Windows/NTFS, where inode semantics differ from Linux):

* **inode/file-id change** -- the path now points at a different file.
* **truncation** -- the file shrank below our current read offset (covers
  ``> file`` style truncation and rotation where inode is unavailable).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator, Optional

# Read in bounded chunks so backfilling a huge file (``--from-start`` on a
# multi-GB log) never pulls the whole backlog into memory at once.
_READ_CHUNK = 65536


def _file_id(stat_result: os.stat_result) -> tuple[int, int]:
    """A best-effort identity for a file across platforms.

    On Linux this is ``(st_dev, st_ino)``. On Windows/NTFS Python populates
    ``st_ino`` with the file index for many filesystems; where it is 0 we still
    have truncation detection as a backstop.
    """
    return (stat_result.st_dev, stat_result.st_ino)


async def tail(
    path: Path | str,
    *,
    from_start: bool = False,
    poll_interval: float = 0.1,
    stop: Optional[asyncio.Event] = None,
) -> AsyncIterator[str]:
    """Yield complete lines from ``path``, surviving rotation and truncation.

    Partial trailing writes are buffered until a newline arrives, so a line is
    never emitted half-written. Cancellation (or setting ``stop``) ends the loop
    cleanly.
    """
    path = Path(path)

    # Wait for the file to exist (a tailed log may not be created yet).
    while not path.exists():
        if stop is not None and stop.is_set():
            return
        await asyncio.sleep(poll_interval)

    fh = open(path, "r", encoding="utf-8", errors="replace")
    try:
        file_id = _file_id(os.fstat(fh.fileno()))
        if not from_start:
            fh.seek(0, os.SEEK_END)

        buffer = ""
        while True:
            if stop is not None and stop.is_set():
                return

            chunk = fh.read(_READ_CHUNK)
            if chunk:
                buffer += chunk
                *lines, buffer = buffer.split("\n")  # trailing element is the partial remainder
                for line in lines:
                    yield line
                continue

            # No new data. Before sleeping, check whether the file rotated.
            try:
                st = os.stat(path)
            except FileNotFoundError:
                await asyncio.sleep(poll_interval)
                continue

            rotated = _file_id(st) != file_id
            truncated = st.st_size < fh.tell()
            if rotated or truncated:
                fh.close()
                fh = open(path, "r", encoding="utf-8", errors="replace")
                file_id = _file_id(os.fstat(fh.fileno()))
                buffer = ""
                continue

            await asyncio.sleep(poll_interval)
    finally:
        fh.close()
