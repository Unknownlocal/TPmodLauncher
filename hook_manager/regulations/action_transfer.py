# SPDX-License-Identifier: MPL-2.0
"""Bounded logical actions; ordinary actions still use a single socket frame."""

import base64
import json
import time

if __package__:
    from .framing import REGULATION_FRAME_BYTES, _regulation_pairs, _regulation_constant, regulation_read, regulation_write, regulation_write_encoded
    from .protocol import REGULATION_ACTION_BYTES, REGULATION_RESULT_BYTES, REGULATION_PART_BYTES, regulation_header


def regulation_send_action(connection, message, *, reply=False):
    data = json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    limit = REGULATION_RESULT_BYTES if reply else REGULATION_ACTION_BYTES
    if len(data) > limit:
        raise ValueError("Regulation action exceeds its logical limit")
    if len(data) <= REGULATION_FRAME_BYTES:
        # Sizing already encoded this message. Reuse those exact bytes rather
        # than paying for a second JSON encoding on every ordinary action/reply.
        regulation_write_encoded(connection, data, message["deadline"])
        return
    header = regulation_header(message, reply=reply)
    del header["generations"]
    header["op"] = "action-result-part" if reply else "action-part"
    count = (len(data) + REGULATION_PART_BYTES - 1) // REGULATION_PART_BYTES
    for index in range(count):
        piece = data[index * REGULATION_PART_BYTES:(index + 1) * REGULATION_PART_BYTES]
        # Base64 gives a predictable frame size even when the inner JSON has
        # quotes or non-ASCII text. Every piece keeps the original deadline.
        regulation_write(connection, {**header, "index": index, "parts": count, "bytes": len(data),
                                      "data": base64.b64encode(piece).decode("ascii")}, message["deadline"])


def regulation_receive_action(connection, first, *, reply=False):
    header = regulation_header(first, reply=reply)
    if first["op"] != ("action-result-part" if reply else "action-part"):
        return first
    total, count = first["bytes"], first["parts"]
    limit = REGULATION_RESULT_BYTES if reply else REGULATION_ACTION_BYTES
    if (type(total) is not int or not REGULATION_FRAME_BYTES < total <= limit
            or type(count) is not int or count != (total + REGULATION_PART_BYTES - 1) // REGULATION_PART_BYTES
            or not 2 <= count <= (limit + REGULATION_PART_BYTES - 1) // REGULATION_PART_BYTES):
        raise ValueError("Invalid regulation fragment plan")
    # Allocate incrementally only after the finite plan has passed. One buffer
    # holds decoded bytes; partial fragments never reach the controller/guest.
    data = bytearray()
    for index in range(count):
        part = first if index == 0 else regulation_read(connection, header["deadline"])
        if (regulation_header(part) != header or type(part["index"]) is not int or part["index"] != index
                or type(part["parts"]) is not int or part["parts"] != count
                or type(part["bytes"]) is not int or part["bytes"] != total or type(part["data"]) is not str):
            raise ValueError("Foreign or reordered regulation fragment")
        expected = min(REGULATION_PART_BYTES, total - len(data))
        # Check the declared encoded length before allocating decoded bytes,
        # then check the actual result as padding can change the decoded length.
        if len(part["data"]) != 4 * ((expected + 2) // 3):
            raise ValueError("Invalid regulation fragment length")
        piece = base64.b64decode(part["data"], validate=True)
        if len(piece) != expected:
            raise ValueError("Invalid decoded regulation fragment length")
        data.extend(piece)
    # Parse once after all pieces arrive. Matching fragment headers alone is
    # insufficient: the completed inner action must carry the same identity.
    result = json.loads(data, object_pairs_hook=_regulation_pairs, parse_constant=_regulation_constant)
    header["op"] = "action"
    completed = regulation_header(result, reply=reply)
    del completed["generations"]
    if completed != header or time.monotonic() >= header["deadline"]:
        raise ValueError("Foreign or late completed regulation action")
    return result
