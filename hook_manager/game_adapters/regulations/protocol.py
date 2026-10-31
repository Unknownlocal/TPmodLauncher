# SPDX-License-Identifier: MPL-2.0
"""Fixed private v2 envelopes, shared by the controller and injected client."""

import math
import json
import time

from .framing import regulation_encode


def regulation_native_option(value):
    """Combine operational fields; each caller still checks values and size limits."""
    if type(value) is not dict or type(value.get("key")) is not str or not 1 <= len(value["key"]) <= 128:
        raise ValueError("Invalid effective regulation option")
    parameters = value.get("parameters", {})
    if type(parameters) is not dict:
        raise ValueError("Invalid native regulation parameters")
    # Saved replacements and synthesised choices may add operational fields.
    # Preserve those fields while leaving display metadata out of rule data.
    flat = {key: item for key, item in value.items()
            if key not in ("id", "key", "name", "summary", "description", "parameters")}
    if flat.keys() & parameters.keys():
        raise ValueError("Native flat and explicit parameters collide")
    flat.update(parameters)
    return {"key": value["key"], "parameters": flat}


def regulation_policy_value(kind, value):
    """Normalise native rule data; custom values are admitted keys."""
    if kind == "native":
        value = regulation_native_option(value)
        payload = value["parameters"]
    elif kind == "custom" and type(value) is str and 1 <= len(value) <= 128:
        payload = value
    else:
        raise ValueError("Invalid custom regulation reference")
    nodes, pending = 0, [(payload, 0)]
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 1024 or (type(item) in (dict, list) and depth >= 8):
            raise ValueError("Regulation record exceeds logical bounds")
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Non-string regulation parameter key")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) not in (str, int, float, bool, type(None)):
            raise ValueError("Non-JSON regulation parameter")
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > 4096:
        raise ValueError("Regulation record exceeds logical bytes")
    # Wire bytes include the normalized option wrapper/key; nodes count the
    # payload selected above. A custom wire entry is only a key: its rule data
    # comes from admitted definitions rather than this native publication.
    size = len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))
    return value, size, nodes


def regulation_integer(value, *, positive=False):
    if type(value) is not int or not (int(positive) <= value < 2**64):
        raise ValueError("Invalid regulation identity")
    return value


def regulation_header(message, *, check_deadline=True, reply=False):
    """Validate closed operation shapes before queueing or allocating tables."""
    common = {"version", "token", "id", "op", "epoch", "deadline", "generations"}
    additions = {
        "policy-begin": {"plan"}, "policy-page": {"page", "rows"}, "policy-seal": set(),
        "reset": set(), "action": {"action", "contexts", "selections", "owners"},
        "vote": {"owner", "snapshot", "regulation", "contexts"},
    }
    if type(message) is not dict or type(message.get("op")) is not str or message["op"] not in additions:
        raise ValueError("Unknown regulation operation")
    if reply:
        # Replies echo correlation, not the request's context/policy body. Keep
        # success and fixed-error shapes closed under the same header checks.
        identity = {"action"} if message["op"] == "action" else {"page"} if message["op"] == "policy-page" else set()
        additions[message["op"]] = identity | ({"result"} if "result" in message else {"error", "unavailable"})
    if set(message) != common | additions[message["op"]] or type(message["version"]) is not int or message["version"] != 2:
        raise ValueError("Invalid regulation protocol version or fields")
    for key in ("id", "epoch"):
        regulation_integer(message[key], positive=True)
    token = message["token"]
    if type(token) is not str or len(token) != 64 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError("Invalid regulation authentication")
    generations = message.get("generations", {})
    if type(generations) is not dict or len(generations) > 32:
        raise ValueError("Invalid regulation owner generations")
    for owner, generation in generations.items():
        if type(owner) is not str or not 1 <= len(owner) <= 128:
            raise ValueError("Invalid regulation owner")
        # Reset may precede policy installation. Only that operation can refer
        # to generation zero; actions always require acknowledged positive IDs.
        regulation_integer(generation, positive=message["op"] != "reset")
    limit = .1 if message["op"] == "action" else 5 if message["op"] == "vote" else 1 + 5 * len(generations)
    deadline = message["deadline"]
    # The controller may need to construct an error header for an expired queued
    # request. Disabling this check validates its shape only; poll() still refuses
    # expired execution before entering any handler.
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or (check_deadline and not 0 < deadline - time.monotonic() <= limit)):
        raise ValueError("Invalid regulation original deadline")
    header = {key: message[key] for key in common}
    if message["op"] == "action":
        regulation_integer(message["action"], positive=True)
        header["action"] = message["action"]
    if message["op"] == "action" and not reply:
        contexts, selections, owners = message["contexts"], message["selections"], message["owners"]
        # Current adapters check one proposal, with at most the session's 256
        # declared validators. Reject impossible tables before queueing or encoding.
        if (type(contexts) is not list or len(contexts) != 1
                or type(selections) is not list or not 1 <= len(selections) <= 256
                or type(owners) is not dict or not owners or owners.keys() != generations.keys()
                or any(type(row) is not dict or type(row.get("calls")) is not list
                       or not 1 <= len(row["calls"]) <= 32 for row in owners.values())
                or sum(len(row["calls"]) for row in owners.values()) > 256):
            raise ValueError("Invalid single-proposal regulation action")
    if message["op"] == "vote" and not reply:
        if (type(message["owner"]) is not str or set(generations) != {message["owner"]}
                or type(message["snapshot"]) is not dict or type(message["regulation"]) is not dict
                or type(message["contexts"]) is not list or not 1 <= len(message["contexts"]) <= 64
                or len(regulation_encode(message)) > 512 * 1024):
            raise ValueError("Invalid voting batch")
    if message["op"] == "policy-page":
        regulation_integer(message["page"])
        header["page"] = message["page"]
    return header


def regulation_votes(value, teams):
    """Check every addressed vote before native code records any result."""
    if type(value) is not dict or value.keys() != set(teams):
        raise ValueError("Incomplete voting results")
    for vote in value.values():
        if type(vote) is not dict or set(vote) != {"choice", "reason"}:
            raise ValueError("Invalid voting result fields")
        reason = vote["reason"]
        if (type(vote["choice"]) is not str or vote["choice"] not in ("current", "proposed")
                or type(reason) is not str or not reason.strip() or len(reason.encode("utf-8")) > 2048
                or any(not char.isprintable() and char not in "\n\t" for char in reason)):
            raise ValueError("Invalid voting choice or reason")
    return value


def regulation_pages(rows):
    """Bound one policy page without serializing the entire publication."""
    page, size = [], 2
    for row in rows:
        length = len(regulation_encode(row)) - 4 + 1
        if page and size + length > 64 * 1024:
            yield page
            page, size = [], 2
        if length > 64 * 1024:
            raise ValueError("Regulation policy row exceeds its bound")
        page.append(row)
        size += length
    if page:
        yield page
