# SPDX-License-Identifier: MPL-2.0
"""Convert a selected owner section to a regulation model; never import code."""

from collections import ChainMap

from launcher.json_ingest import materialize
from . import payload, schema
from .bridge_converter import convert_bridges
from .owner_definition import OwnerDefinition


def _categories(owner, rows, base):
    """Index owned categories; session admission checks other owners afterward."""
    categories = {}
    for row in schema.rows(rows, 32):
        key = schema.category(owner, row)
        if key in base or key in categories:
            raise ValueError("Custom category collides with an existing definition")
        categories[key] = row
    return categories


def _metadata(value, accessible, index):
    """Check category/option references while leaving author payloads opaque."""
    if type(value) is not dict:
        raise ValueError("Option data must be an owned category/option map")
    for key, options in value.items():
        if key not in accessible or type(options) is not dict:
            raise ValueError("Option data targets an unknown or foreign category")
        known = index.option_keys(key, accessible[key])
        for option, parameters in options.items():
            if option not in known:
                raise ValueError("Option data targets an unknown option")
            schema.parameters(parameters)
    return value


def _validators(value, source, accessible, scopes, index):
    validators = schema.rows(value, 32)
    # Validator IDs are local to an owner; another mod may reuse the same ID.
    identities = set()
    owned_paths = index.source_paths(source) if validators else frozenset()
    for row in validators:
        schema.validator(row, owned_paths, accessible, scopes)
        if row["id"] in identities:
            raise ValueError("Duplicate validator identity")
        identities.add(row["id"])
    return validators


def _scripts(owner, value, categories, index):
    """Normalize owned scripts; session admission reserves identities and seasons."""
    rows = schema.rows(value, 128)
    known = {key: index.option_keys(key, category) for key, category in categories.items()} if rows else {}
    # The lookup contains owned categories only, even if the shared index also
    # contains native or another owner's keys. Fallback IDs use declaration order.
    return [schema.script(owner, row, known, position) for position, row in enumerate(rows)]


def _payloads(model, accessible, index):
    # Native option parameters/envelopes are measured once per referenced category
    # for this attempt. Each owner must still account for its own option_data.
    for key in {row["key"] for row in model.categories} | model.rule_keys():
        for option, base in index.payloads(key, accessible[key]):
            payload.check_metadata(base, model.option_data.get(key, {}).get(option, {}))


def convert_owner(owner, section, source, base, scopes, index):
    """Validate one enabled subtree before transferring its data to a model.

    The index caches derived data only; this function cannot publish a model.
    Session admission checks shared collisions/quotas after conversion succeeds.
    """
    try:
        # Strict duplicate checks apply only to this selected enabled subtree.
        # Materialization also creates the containers this owner's model will own.
        data = materialize(section)
        schema.fields(data, ("schema",), ("categories", "option_data", "scripted_changes", "validators", "native_rules"))
        if type(data["schema"]) is not int or data["schema"] != 1:
            raise ValueError("Unsupported enabled regulation schema")
        categories = _categories(owner, data.get("categories", []), base)
        # References can see native/fallback rules and this owner's categories.
        # Previously processed owners' custom categories never become accessible.
        accessible = ChainMap(categories, base)
        metadata = _metadata(data.get("option_data", {}), accessible, index)
        validators = _validators(data.get("validators", []), source, accessible, scopes, index)
        scripts = _scripts(owner, data.get("scripted_changes", []), categories, index)
        bridges = convert_bridges(data.get("native_rules", {}), accessible)
        model = OwnerDefinition(list(categories.values()), metadata, validators, scripts, bridges)
        _payloads(model, accessible, index)
        return model
    except (ValueError, TypeError, KeyError) as error:
        # Keep the owner identity on declaration failures; the document converter
        # adds the preparation-level error and discards the entire attempt.
        raise ValueError(f"Regulation owner {owner}: {error}") from error
