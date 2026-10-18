# SPDX-License-Identifier: MPL-2.0
"""Check reusable settings fields, isolated persistence and filesystem boundaries."""

import json
import os

import pytest

from launcher import ui_settings as settings


DEFINITION = {"schema": 1, "label": "Theme", "fields": [
    {"id": "colour_11", "label": "Custom Theme Colour 11", "color": "#3070b9"},
    {"id": "colour_52", "label": "Custom Theme Colour 52", "color": "#000000a0"},
]}
VALUES = {"schema": 1, "values": {"colour_11": "#abcdef", "colour_52": "#10203080"}}


@pytest.fixture
def store(tmp_path):
    root = tmp_path / "owned mod"
    root.mkdir()
    return settings.UISettings(root)


def test_save_normalizes_hex_and_a_new_session_loads_saved_values(store):
    store.register(json.dumps(DEFINITION))
    result = json.loads(store.save(json.dumps(VALUES)))
    assert result["values"] == {"colour_11": "#ABCDEF", "colour_52": "#10203080"}
    assert json.loads((store.root / "settings.json").read_text()) == result
    fresh = settings.UISettings(store.root)
    assert json.loads(fresh.register(json.dumps(DEFINITION))) == result
    assert store.register(json.dumps(DEFINITION)) == fresh.register(json.dumps(DEFINITION))
    assert list(store.root.iterdir()) == [store.root / "settings.json"]


def test_write_failure_keeps_previous_settings_and_removes_temporary_file(store, monkeypatch):
    store.register(json.dumps(DEFINITION))
    previous = store.save(json.dumps(VALUES))
    before = (store.root / "settings.json").read_bytes()
    def fail(*args, **kwargs):
        raise OSError("owned write failure")
    if os.name == "nt":
        monkeypatch.setattr(settings.files._WindowsFiles, "rename", fail)
    else:
        monkeypatch.setattr(settings.files.os, "replace", fail)
    with pytest.raises(OSError, match="owned write failure"):
        store.save(json.dumps({"schema": 1, "values": {"colour_11": "#FFFFFF", "colour_52": "#00000000"}}))
    assert store.register(json.dumps(DEFINITION)) == previous
    assert (store.root / "settings.json").read_bytes() == before
    assert list(store.root.iterdir()) == [store.root / "settings.json"]


def test_hardlinked_settings_cannot_be_overwritten(store, tmp_path):
    store.register(json.dumps(DEFINITION))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(VALUES))
    os.link(outside, store.root / "settings.json")
    before = outside.read_bytes()
    with pytest.raises(ValueError, match="unshared"):
        store.save(json.dumps(VALUES))
    assert outside.read_bytes() == before


def test_owners_keep_private_values_and_cleanup_only_removes_registrations(tmp_path):
    registry = settings.SettingsRequests()
    for owner in ("owned.first", "owned.second"):
        root = tmp_path / owner
        root.mkdir()
        registry.bind(owner, root)
        registry.call(owner, "register-settings", json.dumps(DEFINITION | {"label": owner}))
    first = registry.call("owned.first", "save-settings", json.dumps(VALUES))
    assert registry.records[0]["fields"][0]["value"] == "#ABCDEF"
    assert registry.records[1]["fields"][0]["value"] == "#3070B9"
    registry.remove("owned.first")
    assert [row["owner"] for row in registry.records] == ["owned.second"]
    assert json.loads((tmp_path / "owned.first/settings.json").read_text()) == json.loads(first)
    assert not (tmp_path / "owned.second/settings.json").exists()
