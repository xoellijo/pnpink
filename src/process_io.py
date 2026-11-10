# -*- coding: utf-8 -*-
"""Efficient incremental decoding for subprocess text streams."""

from __future__ import annotations

import codecs
import io
from typing import Iterator, TextIO


def iter_text_chunks(stream: TextIO, chunk_size: int = 4096) -> Iterator[str]:
    """Yield currently available subprocess output without per-character I/O."""
    raw = getattr(stream, "buffer", None)
    read1 = getattr(raw, "read1", None)
    if not callable(read1) and isinstance(raw, io.RawIOBase):
        # Popen(bufsize=0, text=True) exposes a FileIO, not a BufferedReader.
        # Its read() returns available pipe bytes without waiting to fill the
        # requested size. Falling back to text.read(1) amplifies every warning
        # into thousands of queue operations and log writes on Windows.
        read1 = raw.read
    if callable(read1):
        decoder = codecs.getincrementaldecoder(stream.encoding or "utf-8")(errors="replace")
        while data := read1(max(256, int(chunk_size))):
            if text := decoder.decode(data):
                yield text
        if tail := decoder.decode(b"", final=True):
            yield tail
        return

    while True:
        text = stream.read(1)
        if not text:
            return
        yield text
