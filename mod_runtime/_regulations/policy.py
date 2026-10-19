# SPDX-License-Identifier: MPL-2.0
"""Plain bounded regulation wire values; shared by trusted host and frozen guest."""

import json
import math

MAX_CALLS = 4  # Bound guest preparation to four independently copied envelopes.


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


def _option(value):
    fields(value, ("key", "effective_season", "parameters", "option_data"))
    _text(value["key"])
    _integer(value["effective_season"])
    if type(value["parameters"]) is not dict or type(value["option_data"]) is not dict:
        raise ValueError("Expected regulation parameter objects")


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


def invocation(context, regulation):
    """Copy and bound the expanded envelope, not its deduplicated batch encoding.

    Common envelope fields are checked here. Scope-specific proposal normalization
    belongs to the trusted semantic adapter, never the generic runner or a mod.
    """
    nodes = 0

    def copy(value, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 1024:
            raise ValueError("Regulation invocation exceeds its node limit")
        kind = type(value)
        if kind in (dict, list):
            if depth >= 8:
                raise ValueError("Regulation invocation exceeds its container depth")
            if kind is dict:
                result = {}
                # Check keys during copying, avoiding a second traversal and a
                # separate generator for every object in a nested envelope.
                for key, item in value.items():
                    if type(key) is not str:
                        raise ValueError("Regulation object keys must be strings")
                    result[key] = copy(item, depth + 1)
                return result
            return [copy(item, depth + 1) for item in value]
        if value is None or kind in (str, bool, int):
            return value
        if kind is float and math.isfinite(value):
            return value
        raise ValueError("Expected finite plain regulation JSON values")

    result = copy({"context": context, "regulation": regulation})
    if len(encode(result).encode("utf-8")) > 4096:
        raise ValueError("Regulation invocation exceeds 4096 UTF-8 bytes")
    context, regulation = result["context"], result["regulation"]
    context_fields(context)
    fields(regulation, ("schema", "key", "revision", "evaluation_season", "selected", "current", "upcoming"))
    if type(regulation["schema"]) is not int or regulation["schema"] != 1:
        raise ValueError("Unsupported regulation snapshot schema")
    _text(regulation["key"])
    _text(regulation["revision"])
    _integer(regulation["evaluation_season"])
    if regulation["evaluation_season"] != context["effective"]["season"]:
        raise ValueError("Regulation evaluation season differs from the proposal")
    _option(regulation["current"])
    if regulation["upcoming"] is not None:
        _option(regulation["upcoming"])
    if (regulation["selected"] not in ("current", "upcoming")
            or regulation[regulation["selected"]] is None):
        raise ValueError("Unavailable selected regulation option")
    return context, regulation
