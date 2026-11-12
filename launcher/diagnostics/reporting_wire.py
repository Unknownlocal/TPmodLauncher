# SPDX-License-Identifier: MPL-2.0
"""Frame local reporting messages without limiting complete diagnostic length."""

import json
import select
import struct
from time import monotonic

REPORTING_FRAME_BYTES = 64 * 1024
REPORTING_TRANSFER_SECONDS = 5.0


def reporting_transfer(connection, count, deadline, *, outgoing=None, interrupt=None):
    """Wait for actual socket readiness, with optional cancellation and one deadline."""
    result, offset = bytearray(), 0
    while offset < count:
        remaining = None if deadline is None else deadline - monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Native reporting transfer timed out")
        readers = ([connection] if outgoing is None else []) + ([interrupt] if interrupt is not None else [])
        readable, writable, _ = select.select(readers, [connection] if outgoing is not None else [], [], remaining)
        if interrupt is not None and interrupt in readable:
            raise ConnectionAbortedError("Native reporting stopped")
        if outgoing is None and connection in readable:
            block = connection.recv(count - offset)
            if not block:
                raise EOFError("Native reporting connection closed")
            result.extend(block)
            offset += len(block)
        elif outgoing is not None and writable:
            sent = connection.send(memoryview(outgoing)[offset:])
            if not sent:
                raise EOFError("Native reporting connection closed while writing")
            offset += sent
    return result


def reporting_write(connection, sequence, value):
    """Split a complete record into correlated, size-limited frames, without truncation."""
    data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
    deadline = monotonic() + REPORTING_TRANSFER_SECONDS
    for start in range(0, len(data), REPORTING_FRAME_BYTES):
        block = data[start:start + REPORTING_FRAME_BYTES]
        frame = struct.pack("!QBI", sequence, int(start + len(block) == len(data)), len(block)) + block
        reporting_transfer(connection, len(frame), deadline, outgoing=frame)


def reporting_read(connection, *, interrupt=None, deadline=None, limit=None):
    """Block indefinitely while idle; bound an incomplete message from its first byte."""
    data, expected = bytearray(), None
    while True:
        first = reporting_transfer(connection, 1, deadline, interrupt=interrupt)
        if deadline is None:
            deadline = monotonic() + REPORTING_TRANSFER_SECONDS
        # Each chunk repeats its message number; the final bit ends that JSON
        # message. Continuations share the original partial-transfer deadline.
        header = first + reporting_transfer(connection, 12, deadline, interrupt=interrupt)
        sequence, final, size = struct.unpack("!QBI", header)
        if (final not in (0, 1) or not 1 <= size <= REPORTING_FRAME_BYTES
                or expected is not None and sequence != expected):
            raise ValueError("Invalid native reporting frame")
        expected = sequence
        if limit is not None and len(data) + size > limit:
            raise ValueError("Native reporting handshake exceeds its byte limit")
        data.extend(reporting_transfer(connection, size, deadline, interrupt=interrupt))
        if final:
            return sequence, json.loads(data)
