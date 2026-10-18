# SPDX-License-Identifier: MPL-2.0
"""Convert a selected owner section to a regulation model; never import code."""

from launcher.json_ingest import materialize
from . import schema
from .owner_definition import OwnerDefinition


def _categories(owner, rows, reserved):
    """Index owned categories and reserve their keys against the whole session."""
    categories = {}
    for row in schema.rows(rows, 32):
        key = schema.category(owner, row)
        if key in reserved:
            raise ValueError("Custom category collides with an existing definition")
        reserved.add(key)
        categories[key] = row
    return categories


def _metadata(value, accessible):
    """Check category/option references while leaving author payloads opaque."""
    if type(value) is not dict:
        raise ValueError("Option data must be an owned category/option map")
    for key, options in value.items():
        if key not in accessible or type(options) is not dict:
            raise ValueError("Option data targets an unknown or foreign category")
        known = schema.option_keys(accessible[key])
        for option, parameters in options.items():
            if option not in known:
                raise ValueError("Option data targets an unknown option")
            schema.parameters(parameters)
    return value


def _validators(value, source, accessible, scopes):
    validators = schema.rows(value, 32)
    # Validator IDs are local to an owner; another mod may reuse the same ID.
    identities = set()
    for row in validators:
        schema.validator(row, source, accessible, scopes)
        if row["id"] in identities:
            raise ValueError("Duplicate validator identity")
        identities.add(row["id"])
    return validators


def _scripts(owner, value, categories, script_ids, script_targets):
    """Normalize owned script IDs and reserve each change against native/owner rows."""
    scripts = []
    for index, row in enumerate(schema.rows(value, 128)):
        # schema.script returns a new row with an owner-qualified ID; the source
        # declaration order supplies its fallback ID when the author omits one.
        row = schema.script(owner, row, categories, index)
        if row["id"] in script_ids:
            raise ValueError("Duplicate scripted change identity")
        target = (row["key"], row["effective_season"])
        if target in script_targets:
            raise ValueError("Duplicate scripted change category/season")
        script_ids.add(row["id"])
        script_targets.add(target)
        scripts.append(row)
    return scripts


def _payloads(categories, metadata, validators, accessible):
    # Check a lower bound for each possible current option. Actual contexts and
    # any upcoming record must still fit in the dispatch-time envelope.
    # Visit each relevant category once even when several validators use it.
    for key in categories.keys() | metadata.keys() | {row["rule"] for row in validators}:
        for option in accessible[key]["options"]:
            schema.parameters(option.get("parameters", {}))
            schema.payload_budget({"context": {}, "regulation": {
                "schema": 1, "key": key, "revision": "x", "evaluation_season": 0,
                "selected": "current", "upcoming": None, "current": {
                    "key": option["key"], "effective_season": 0,
                    "parameters": option.get("parameters", {}),
                    "option_data": metadata.get(key, {}).get(option["key"], {})}}})


def convert_owner(owner, section, source, base, scopes, reserved, script_ids, script_targets):
    """Validate one enabled subtree before transferring its data to a model.

    Session collision sets are local to the enclosing conversion. A failure
    discards that entire preparation, including all provisional reservations.
    """
    try:
        # Strict duplicate checks apply only to this selected enabled subtree.
        # Materialization also creates the containers this owner's model will own.
        data = materialize(section)
        schema.fields(data, ("schema",), ("categories", "option_data", "scripted_changes", "validators"))
        if type(data["schema"]) is not int or data["schema"] != 1:
            raise ValueError("Unsupported enabled regulation schema")
        categories = _categories(owner, data.get("categories", []), reserved)
        # References can see native/fallback rules and this owner's categories.
        # Previously processed owners' custom categories never become accessible.
        accessible = {**base, **categories}
        metadata = _metadata(data.get("option_data", {}), accessible)
        validators = _validators(data.get("validators", []), source, accessible, scopes)
        scripts = _scripts(owner, data.get("scripted_changes", []), categories, script_ids, script_targets)
        _payloads(categories, metadata, validators, accessible)
        return OwnerDefinition(list(categories.values()), metadata, validators, scripts)
    except (ValueError, TypeError, KeyError) as error:
        # Keep the owner identity on declaration failures; the document converter
        # adds the preparation-level error and discards the entire attempt.
        raise ValueError(f"Regulation owner {owner}: {error}") from error
