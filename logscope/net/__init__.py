"""Distributed ingestion: agents ship events to a central server."""

from logscope.net.protocol import (
    PROTOCOL_VERSION,
    decode_batch,
    encode_batch,
    read_frame,
    write_frame,
)

__all__ = [
    "PROTOCOL_VERSION",
    "encode_batch",
    "decode_batch",
    "read_frame",
    "write_frame",
]
