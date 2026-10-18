# SPDX-License-Identifier: MPL-2.0
"""Validate declaration structure, never condition meanings or native bindings."""

import json
import re

from mod_runtime._source_policy import module_part

MAX_FILE_BYTES = 1024 * 1024


def name(value, maximum=128):
    if type(value) is not str or len(value) > maximum or not re.fullmatch(r"[a-z][a-z0-9_.:-]*", value):
        raise ValueError("Expected a named regulation identifier")
    return value


def fields(value, required, optional=()):
    # Regulation declarations have a closed field set. Opaque parameters below
    # deliberately bypass this rule so authors can define their own condition data.
    if type(value) is not dict or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Invalid regulation declaration fields")


def rows(value, maximum):
    if type(value) is not list or len(value) > maximum:
        raise ValueError("Invalid regulation declaration count")
    return value


def label(value):
    if type(value) is not str or not 1 <= len(value) <= 128 or value != value.strip() or not value.isprintable():
        raise ValueError("Expected a short printable regulation label")


def parameters(value):
    """Require an object without interpreting its keys or condition semantics."""
    if type(value) is not dict:
        raise ValueError("Regulation parameters must be an object")
    return value


def category(owner, value):
    fields(value, ("key", "label", "default", "options"), ("description", "neighbours"))
    key = name(value["key"], 256)
    if not key.startswith(owner + "."):
        raise ValueError("Custom category leaves its owner's namespace")
    label(value["label"])
    options = rows(value["options"], 32)
    if not options:
        raise ValueError("Custom category needs at least one option")
    keys, ids = set(), set()
    for option in options:
        fields(option, ("id", "key", "name"), ("summary", "description", "parameters"))
        option_key = name(option["key"])
        if option_key in keys or type(option["id"]) is not int or option["id"] in ids:
            raise ValueError("Duplicate or invalid regulation option identity")
        keys.add(option_key)
        ids.add(option["id"])
        label(option["name"])
        parameters(option.get("parameters", {}))
        for text in ("summary", "description"):
            if text in option and type(option[text]) is not str:
                raise ValueError("Regulation option text must be a string")
    # Native consumers expect consecutive 1-based IDs and a resolvable default.
    # Exact int checks above also exclude booleans, which Python treats as ints.
    if ids != set(range(1, len(options) + 1)) or value["default"] not in keys:
        raise ValueError("Options need consecutive positive IDs and a valid default")
    if "description" in value and type(value["description"]) is not str:
        raise ValueError("Regulation description must be a string")
    neighbours = value.get("neighbours", {})
    if type(neighbours) is not dict:
        raise ValueError("Regulation neighbours must be an object")
    # Adjacency may be directed, but every edge must reach a distinct known option.
    for option, adjacent in neighbours.items():
        if (option not in keys or type(adjacent) is not list or any(type(item) is not str for item in adjacent)
                or len(adjacent) != len(set(adjacent)) or any(item not in keys or item == option for item in adjacent)):
            raise ValueError("Invalid regulation neighbours")
    return key


def option_keys(category):
    # Native declarations stay unchanged. Only index identities needed to check
    # extension references; native normalization/fallbacks remain game-owned.
    if type(category) is not dict or type(category.get("options")) is not list:
        raise ValueError("Unavailable native regulation options")
    return {name(option["key"]) for option in category["options"]}


def validator(value, source, accessible, scopes):
    """Check a semantic rule/context and an owned source reference, without binding."""
    fields(value, ("id", "rule", "scope", "handler"))
    name(value["id"])
    if type(value["rule"]) is not str or value["rule"] not in accessible:
        raise ValueError("Validator targets an unknown or foreign category")
    if type(value["scope"]) is not str or value["scope"] not in scopes:
        raise ValueError("Validator context has no trusted mapping")
    handler = value["handler"]
    if type(handler) is not str or not 3 <= len(handler) <= 256:
        raise ValueError("Expected an owned Python handler reference")
    parts = handler.split(".")
    if len(parts) < 2:
        raise ValueError("Handler needs a module and function")
    for part in parts:
        module_part(part)
    # The final segment names a function; preceding segments name an owner-local
    # module/package path. This lookup never resolves a module in the host process.
    module = "/".join(parts[:-1])
    owned = {path for path, _ in source.files}
    if module + ".py" not in owned and module + "/__init__.py" not in owned:
        raise ValueError("Handler module is absent from its owner's source snapshot")
    # Function existence/signature, imports and execution are deliberately left
    # to sandbox binding. Source text is never imported or evaluated by this host.


def script(owner, value, categories, index):
    fields(value, ("key", "new_regulation", "effective_season"), ("id",))
    key = value["key"]
    if type(key) is not str or key not in categories:
        raise ValueError("Scripted change must target an owned custom category")
    # Native policy validation consumes an option object, not the prototype's
    # shorthand string. Keep that format so later governance sees the same data.
    fields(value["new_regulation"], ("key",))
    option = value["new_regulation"]["key"]
    if type(option) is not str or option not in option_keys(categories[key]):
        raise ValueError("Scripted change targets an unknown option")
    season = value["effective_season"]
    if type(season) is not int or season < 1:
        raise ValueError("Scripted change needs a positive effective season")
    # Qualify both explicit IDs and positional fallbacks before session collision
    # checking, so two owners can each use an author-local ID such as "first".
    identity = name(value["id"]) if "id" in value else str(index)
    return {**value, "id": owner + ":" + identity}


def payload_budget(value):
    """Reject statically impossible policy data; full contexts are checked later."""
    nodes = 0

    def visit(item, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 1024:
            raise ValueError("Regulation payload exceeds its node limit")
        # Count values (including the root) as nodes, and only containers toward
        # depth. Object keys contribute to UTF-8 size in the serialization below.
        if type(item) in (dict, list):
            depth += 1
            if depth > 8:
                raise ValueError("Regulation payload exceeds its container depth")
            for child in item.values() if type(item) is dict else item:
                visit(child, depth)

    visit(value)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > 4096:
        raise ValueError("Regulation payload exceeds 4096 UTF-8 bytes")
