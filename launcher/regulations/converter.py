# SPDX-License-Identifier: MPL-2.0
"""Select regulation data explicitly and prepare model state atomically."""

from copy import deepcopy

from launcher.json_ingest import JsonObject, decode
from mod_runtime.source import SourceSnapshot
from . import schema
from .owner_converter import convert_owner
from .preparation_index import PreparationIndex
from .revisions import finalize
from .session import RegulationSession

_MISSING = object()


def admitted_sources(enabled_sources):
    """Accept only trusted snapshots; JSON owner claims cannot admit code."""
    if type(enabled_sources) is not dict or len(enabled_sources) > 32:
        raise ValueError("Expected at most 32 admitted regulation source owners")
    for owner, source in enabled_sources.items():
        schema.name(owner)
        if (type(source) is not SourceSnapshot or source.manifest.id != owner
                or source.manifest.api != "0.4.0" or "regulations" not in source.manifest.capabilities):
            raise ValueError("Regulation owner needs its admitted source snapshot and capability")
    return dict(enabled_sources)


def _native(raw, native_categories):
    """Return the preserved native document and a separate reference catalogue."""
    # Native json.loads uses the last value of duplicate fields. Opaque native
    # fields survive materialization; only consumed extensions use strict keys.
    native = raw.materialize(exclude=("mod_extensions",), duplicates="last")
    if type(native.get("categories", [])) is not list:
        raise ValueError("Native categories must be an array")
    base = {}
    # Index repeated category keys with the game's last-definition precedence,
    # while retaining the original category array in the native document.
    for category in native.get("categories", []):
        base[schema.name(category["key"], 256)] = category
    # File definitions keep precedence over trusted defaults, as in the game.
    # Defaults support reference checks only; do not insert them into the file view.
    for category in native_categories:
        base.setdefault(schema.name(category["key"], 256), deepcopy(category))
    return native, base


def _script_reservations(native):
    """Reserve existing native identities and category/season slots for the session."""
    control = native.get("change_control", {})
    if type(control) is not dict or type(control.get("scripted_changes", [])) is not list:
        raise ValueError("Native change control must retain its object and script array")
    identities, targets = set(), set()
    for row in control.get("scripted_changes", []):
        if type(row) is not dict:
            raise ValueError("Native scripted change must be an object")
        if "id" in row:
            identities.add(row["id"])
        # Different IDs cannot schedule two changes for the same category/season.
        targets.add((row.get("key"), row.get("effective_season")))
    return identities, targets


def convert(content, enabled_sources, *, scopes, native_categories=()):
    """Return prepared native/owner/revision state, or discard all of it.

    The caller explicitly selects this regulation converter. Neither document
    values nor author code can choose a host model, import or native target.
    """
    sources = admitted_sources(enabled_sources)
    if type(scopes) not in (set, frozenset) or any(type(scope) is not str for scope in scopes):
        raise ValueError("Expected a trusted set of mapped regulation contexts")
    try:
        raw = decode(content, max_bytes=schema.MAX_FILE_BYTES, allow_bom=True)
        if type(raw) is not JsonObject:
            raise ValueError("Regulations must be a JSON object")
        native, base = _native(raw, native_categories)
        # Do not even consume the extension envelope without eligible owners.
        # Disabled sections remain raw: their schema and duplicate keys are ignored.
        sections = raw.member("mod_extensions", JsonObject()) if sources else JsonObject()
        if type(sections) is not JsonObject:
            raise ValueError("Enabled regulation extensions need an object envelope")
        # Each attempt owns its lazy derived-data cache and provisional session.
        # Neither survives a failed conversion or a later pack reload.
        index = PreparationIndex()
        script_ids, script_targets = _script_reservations(native)
        session = RegulationSession(base, script_ids, script_targets)
        # Stable order gives deterministic owner processing and merged output.
        for owner, source in sorted(sources.items()):
            section = sections.member(owner, _MISSING)
            if section is _MISSING:
                # Omission is allowed; an explicit null still enters validation.
                continue
            model = convert_owner(owner, section, source, base, scopes, index)
            session.add(owner, model)
        owners = session.finish()
        # Only accepted owners reach revision finalization. All hashes are ready
        # before the aggregate model can take ownership of prepared state.
        revisions = finalize(owners, sources, base)
        return native, owners, revisions
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise ValueError(f"Cannot prepare regulation definitions: {error}") from error
