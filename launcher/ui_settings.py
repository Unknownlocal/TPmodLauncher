# SPDX-License-Identifier: MPL-2.0
"""Validate declarative UI settings and own each mod's fixed local settings file."""

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re

from launcher import _settings_files as files
from mod_runtime import _source_files as disk

MAX_SETTINGS_BYTES = 4096
MAX_SETTINGS_FIELDS = 64
MAX_SETTINGS_TABS = 4


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate settings field")
        result[key] = value
    return result


def _decode(text):
    if type(text) is not str or len(text.encode("utf-8")) > MAX_SETTINGS_BYTES:
        raise ValueError("Expected at most 4096 UTF-8 bytes of settings JSON")
    try:
        return json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError) as error:
        raise ValueError("Invalid settings JSON") from error


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _hex(value, length=None):
    if type(value) is not str or not re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", value):
        raise ValueError("Expected #RRGGBB or #RRGGBBAA")
    if length is not None and len(value) != length:
        raise ValueError("Colour setting must retain its RGB or RGBA format")
    return value.upper()


def _label(value):
    if (type(value) is not str or not 1 <= len(value) <= 64
            or value != value.strip() or not value.isprintable()):
        raise ValueError("Expected a short printable settings label")
    return value


def settings_definition(text):
    """Accept labelled, typed fields with no executable UI or game targets.

    The color member selects the initial supported field type and its default.
    Other field types can extend this tagged definition when implemented.
    """
    data = _decode(text)
    if type(data) is not dict or set(data) != {"schema", "label", "fields"}:
        raise ValueError("Invalid settings definition fields")
    if type(data["schema"]) is not int or not 1 <= data["schema"] <= 65535:
        raise ValueError("Expected a positive settings schema")
    label = _label(data["label"])
    fields = data["fields"]
    if type(fields) is not list or not 1 <= len(fields) <= MAX_SETTINGS_FIELDS:
        raise ValueError("Expected 1-64 UI settings fields")
    result, identifiers = [], set()
    for row in fields:
        if type(row) is not dict or set(row) != {"id", "label", "color"}:
            raise ValueError("Expected a colour field with id, label and color default")
        field_id = row["id"]
        if (type(field_id) is not str or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,63}", field_id)
                or field_id in identifiers):
            raise ValueError("Expected unique named settings identifiers")
        identifiers.add(field_id)
        result.append({"id": field_id, "label": _label(row["label"]), "color": _hex(row["color"])})
    return {"schema": data["schema"], "label": label,
            "fields": result}


def settings_values(text, definition):
    """Validate complete saved values against their registered typed fields."""
    data = _decode(text)
    if type(data) is not dict or set(data) != {"schema", "values"}:
        raise ValueError("Invalid saved settings fields")
    if type(data["schema"]) is not int or data["schema"] != definition["schema"]:
        raise ValueError("Saved settings schema differs from its definition")
    defaults = {row["id"]: row["color"] for row in definition["fields"]}
    if type(data["values"]) is not dict or data["values"].keys() != defaults.keys():
        raise ValueError("Saved settings must contain exactly their registered field IDs")
    return {"schema": data["schema"], "values": {
        field_id: _hex(data["values"][field_id], len(default))
        for field_id, default in defaults.items()
    }}


class UISettings:
    """A trusted mod folder owns settings.json; guests never supply a filesystem path."""

    def __init__(self, root):
        self.root = Path(root).absolute()
        self.definition = self.values = None
        with ExitStack() as stack:
            _, parent = disk.root_directory(stack, self.root)
            info = os.fstat(parent)
            self._identity = info.st_dev, info.st_ino

    @contextmanager
    def _directory(self, *, writable=False):
        with ExitStack() as stack:
            _, parent = disk.root_directory(stack, self.root, writable=writable)
            info = os.fstat(parent)
            if (info.st_dev, info.st_ino) != self._identity:
                raise ValueError("Settings mod folder changed after admission")
            yield parent

    def register(self, text):
        definition = settings_definition(text)
        if self.definition is not None:
            if definition != self.definition:
                raise ValueError("A registered settings definition cannot change during a session")
            return _encode(self.values)
        with self._directory() as parent:
            try:
                content = files.read(self.root, parent, MAX_SETTINGS_BYTES)
            except FileNotFoundError:
                values = {"schema": definition["schema"], "values": {
                    row["id"]: row["color"] for row in definition["fields"]
                }}
            else:
                values = settings_values(content.decode("utf-8"), definition)
        # Failed registration leaves both in-memory state and existing settings intact.
        self.definition, self.values = definition, values
        return _encode(values)

    def save(self, text):
        if self.definition is None:
            raise ValueError("Register UI settings before saving their values")
        values = settings_values(text, self.definition)
        data = (_encode(values) + "\n").encode("utf-8")
        with self._directory(writable=True) as parent:
            # Existing links, special files, shared files and oversized files are rejected.
            try:
                files.read(self.root, parent, MAX_SETTINGS_BYTES)
            except FileNotFoundError:
                pass
            files.write(parent, data)
        self.values = values
        return _encode(values)

    def record(self, owner):
        """Copy typed UI fields and swatch values in their declared display order."""
        if self.definition is None:
            return None
        return {"owner": owner, "label": self.definition["label"],
                "schema": self.definition["schema"], "fields": [
                    {"id": row["id"], "label": row["label"], "type": "color",
                     "default": row["color"], "value": self.values["values"][row["id"]]}
                    for row in self.definition["fields"]
                ]}


class SettingsRequests:
    """Own staged UI settings tabs and storage bindings for one pack session."""

    def __init__(self):
        self._stores = {}

    def bind(self, owner, root):
        if owner in self._stores:
            raise ValueError("Settings owner is already bound")
        self._stores[owner] = UISettings(root)

    def call(self, owner, operation, text):
        store = self._stores[owner]
        if operation == "register-settings":
            others = [other.definition for other in self._stores.values()
                      if other is not store and other.definition is not None]
            if store.definition is None and len(others) >= MAX_SETTINGS_TABS:
                raise ValueError("At most four mod settings tabs can register per process")
            definition = settings_definition(text)
            if any(other["label"] == definition["label"] for other in others):
                raise ValueError("Mod settings tab labels must be unique")
            return store.register(text)
        if operation == "save-settings":
            return store.save(text)
        raise ValueError("Unknown UI settings operation")

    @property
    def records(self):
        return [record for owner, store in sorted(self._stores.items())
                if (record := store.record(owner)) is not None]

    def remove(self, owner):
        self._stores.pop(owner, None)
