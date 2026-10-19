# SPDX-License-Identifier: MPL-2.0
"""Bounded persistent framing shared by the launcher and trusted native client."""

import json
import select
import struct
import time

REGULATION_FRAME_BYTES = 256 * 1024  # Up to 32 expanded 4-KiB calls plus headers.


def _regulation_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate regulation frame field")
        result[key] = value
    return result


def _regulation_constant(value):
    raise ValueError("Invalid regulation frame number")


def regulation_encode(value):
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(data) > REGULATION_FRAME_BYTES:
        raise ValueError("Regulation frame exceeds its byte limit")
    return struct.pack("!I", len(data)) + data


def regulation_decisions(values, count):
    if type(values) is not list or len(values) != count:
        raise ValueError("Incomplete regulation reply")
    for value in values:
        if type(value) is not dict:
            raise ValueError("Invalid regulation decision")
        if value == {"decision": "allow"}:
            continue
        if set(value) != {"decision", "message"} or value["decision"] != "deny":
            raise ValueError("Invalid regulation decision")
        message = value["message"]
        if (type(message) is not str or not 1 <= len(message) <= 160
                or message != message.strip() or not message.isprintable()):
            raise ValueError("Invalid regulation refusal")
    return values


def _regulation_transfer(connection, count, deadline, outgoing=None):
    result, offset = bytearray(), 0
    while offset < count:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise TimeoutError("Regulation absolute deadline expired")
        readable, writable, _ = select.select([connection] if outgoing is None else [],
                                             [connection] if outgoing is not None else [], [], remaining)
        if outgoing is not None and writable:
            sent = connection.send(memoryview(outgoing)[offset:])
            if not sent:
                raise ConnectionError("Regulation connection closed while writing")
            offset += sent
        elif outgoing is None and readable:
            block = connection.recv(count - offset)
            if not block:
                raise ConnectionError("Regulation connection closed while reading")
            result.extend(block)
            offset += len(block)
    return result


def regulation_write(connection, value, deadline):
    data = regulation_encode(value)
    _regulation_transfer(connection, len(data), deadline, outgoing=data)


def regulation_read(connection, deadline):
    # After an authenticated idle wait, a started frame still has a bounded body
    # transfer. Exact lengths avoid assuming TCP packets are message boundaries.
    size, = struct.unpack("!I", _regulation_transfer(connection, 4, deadline))
    if not 1 <= size <= REGULATION_FRAME_BYTES:
        raise ValueError("Invalid regulation frame length")
    body_deadline = time.monotonic() + 0.1 if deadline is None else deadline
    data = _regulation_transfer(connection, size, body_deadline)
    return json.loads(data, object_pairs_hook=_regulation_pairs, parse_constant=_regulation_constant)
