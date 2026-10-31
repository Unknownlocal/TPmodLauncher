# SPDX-License-Identifier: MPL-2.0
"""Decode bounded UTF-8 bytes without document-specific validation or file I/O."""

import json

from .json_object import JsonObject


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def decode(content, *, max_bytes, allow_bom=False):
    """Return indexed objects, arrays and scalars; callers select a model later.

    The byte budget includes any BOM and is checked before text decoding. Parsing
    records duplicates without rejecting their containing objects, so a consumer
    can select subtrees and apply the appropriate materialization policy.
    """
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("JSON byte limit must be a positive integer")
    if type(content) is not bytes or len(content) > max_bytes:
        raise ValueError(f"Expected at most {max_bytes} bytes of UTF-8 JSON")
    try:
        text = content.decode("utf-8-sig" if allow_bom else "utf-8")
        return json.loads(text, object_pairs_hook=JsonObject, parse_constant=_invalid_constant)
    except (ValueError, RecursionError) as error:
        raise ValueError(f"Invalid JSON: {error}") from error
