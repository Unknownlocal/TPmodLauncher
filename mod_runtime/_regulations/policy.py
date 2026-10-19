# SPDX-License-Identifier: MPL-2.0
"""Plain bounded regulation wire values; shared by trusted host and frozen guest."""

import json


def fields(value, names):
    if type(value) is not dict or value.keys() != set(names):
        raise ValueError("Invalid regulation wire fields")


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate regulation wire field")
        value[key] = item
    return value


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _constant(value):
    raise ValueError("Invalid regulation wire number")


def decode(text):
    if type(text) is not str or len(text.encode("utf-8")) > 4096:
        raise ValueError("Regulation wire argument exceeds 4096 UTF-8 bytes")
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant)


def decision(value):
    if type(value) is not dict:
        raise ValueError("Expected a plain regulation decision")
    # Allow has a short form; a refusal must carry the bounded UI message checked
    # below. Returning new dictionaries keeps only the admitted decision fields.
    if value == {"decision": "allow"}:
        return {"decision": "allow"}
    fields(value, ("decision", "message"))
    message = value["message"]
    if (value["decision"] != "deny" or type(message) is not str or not 1 <= len(message) <= 160
            or message != message.strip() or not message.isprintable()):
        raise ValueError("Invalid regulation refusal")
    return {"decision": "deny", "message": message}


def _integer(value, nullable=False):
    # Unknown optional week bounds use None; zero remains an actual integer value.
    if not (type(value) is int or (nullable and value is None)):
        raise ValueError("Expected an integer regulation field")


def _text(value):
    if type(value) is not str or not value:
        raise ValueError("Expected a nonempty regulation field")


def context_fields(context):
    """Check the common context once; semantic adapters own proposal fields."""
    fields(context, ("schema", "scope", "operation", "initiator", "clock", "effective", "proposal"))
    if type(context["schema"]) is not int or context["schema"] != 1:
        raise ValueError("Unsupported regulation context schema")
    _text(context["scope"])
    if context["operation"] not in ("preview", "commit", "activate", "restore"):
        raise ValueError("Unknown regulation operation")
    if context["initiator"] not in ("player", "ai", "system", "unknown"):
        raise ValueError("Unknown regulation initiator")
    fields(context["clock"], ("season", "absolute_week"))
    for value in context["clock"].values():
        _integer(value)
    fields(context["effective"], ("season", "start_week", "end_week"))
    _integer(context["effective"]["season"])
    for key in ("start_week", "end_week"):
        _integer(context["effective"][key], nullable=True)
    if type(context["proposal"]) is not dict:
        raise ValueError("Expected a normalized regulation proposal")
