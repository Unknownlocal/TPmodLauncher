# SPDX-License-Identifier: MPL-2.0
"""Check reusable settings fields, isolated persistence and filesystem boundaries."""

import json
import os
from pathlib import Path
import runpy
import subprocess

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


def test_registration_uses_defaults_without_creating_settings_and_copies_rows(store):
    assert json.loads(store.register(json.dumps(DEFINITION))) == {
        "schema": 1, "values": {"colour_11": "#3070B9", "colour_52": "#000000A0"},
    }
    assert not (store.root / "settings.json").exists()
    record = store.record("owned.mod")
    assert record["fields"][0] == {
        "id": "colour_11", "label": "Custom Theme Colour 11", "type": "color",
        "default": "#3070B9", "value": "#3070B9",
    }
    record["fields"][0]["value"] = "changed externally"
    record["fields"].clear()
    assert len(store.record("owned.mod")["fields"]) == 2


def test_save_normalizes_hex_and_a_new_session_loads_saved_values(store):
    store.register(json.dumps(DEFINITION))
    result = json.loads(store.save(json.dumps(VALUES)))
    assert result["values"] == {"colour_11": "#ABCDEF", "colour_52": "#10203080"}
    assert json.loads((store.root / "settings.json").read_text()) == result
    fresh = settings.UISettings(store.root)
    assert json.loads(fresh.register(json.dumps(DEFINITION))) == result
    assert store.register(json.dumps(DEFINITION)) == fresh.register(json.dumps(DEFINITION))
    assert list(store.root.iterdir()) == [store.root / "settings.json"]


def test_equal_defaults_remain_independent_and_display_order_is_preserved(store):
    # Reusable UI settings must not impose theme-specific grouping or generated labels.
    definition = {"schema": 3, "label": "Widget Options", "fields": [
        {"id": "outline", "label": "Outline", "color": "#000001"},
        {"id": "fill", "label": "Fill", "color": "#000001"},
    ]}
    defaults = json.loads(store.register(json.dumps(definition)))
    edited = {"schema": 3, "values": {"outline": "#123456", "fill": "#ABCDEF"}}
    store.save(json.dumps(edited))
    assert [row["id"] for row in store.record("owned")["fields"]] == ["outline", "fill"]
    assert [row["label"] for row in store.record("owned")["fields"]] == ["Outline", "Fill"]
    assert json.loads(store.register(json.dumps(definition))) == edited
    store.save(json.dumps({"schema": 3, "values": {"outline": "#123456", "fill": "#123456"}}))
    assert len(store.record("owned")["fields"]) == 2
    store.save(json.dumps(defaults))
    assert json.loads(store.register(json.dumps(definition))) == defaults


@pytest.mark.parametrize("mutation", [
    {"schema": True}, {"schema": 0}, {"schema": 1.5}, {"label": " Leading"},
    {"label": "a\nb"}, {"fields": []}, {"path": "../elsewhere.json"},
    {"fields": [{"id": True, "label": "Accent", "color": "#112233"}]},
    {"fields": [{"id": "../escape", "label": "Accent", "color": "#112233"}]},
    {"fields": [{"id": "a", "label": "A", "color": "#112233"},
                {"id": "a", "label": "B", "color": "#445566"}]},
    {"fields": [{"id": "accent", "label": "Accent", "color": "red"}]},
    {"fields": [{"id": "accent", "label": "Accent", "color": "#12345"}]},
    {"fields": [{"id": "accent", "label": "Accent", "color": "#112233", "callback": "anything"}]},
    {"fields": [{"id": f"field{n}", "label": "A", "color": "#112233"} for n in range(65)]},
    {"fields": [{"id": "accent", "label": " ", "color": "#112233"}]},
    {"fields": [{"id": "accent", "label": "a\nb", "color": "#112233"}]},
    {"fields": [{"id": "accent", "label": "A", "toggle": True}]},
    {"fields": [{"id": "a" * 65, "label": "A", "color": "#112233"}]},
])
def test_invalid_declarations_leave_registration_empty(store, mutation):
    with pytest.raises(ValueError):
        store.register(json.dumps(DEFINITION | mutation))
    assert store.record("owned") is None
    assert not list(store.root.iterdir())


@pytest.mark.parametrize("text", [
    '[', 'null', '{"schema":1,"schema":2}', '[' * 2000,
    '"' + '\u6c34' * 1500 + '"', ' ' * 4097,
])
def test_malformed_duplicate_or_over_budget_json_is_rejected(store, text):
    with pytest.raises(ValueError):
        store.register(text)
    assert store.definition is None


@pytest.mark.parametrize("mutation", [
    {"schema": 2}, {"schema": True}, {"values": {"colour_11": "#ABCDEF"}},
    {"values": {"colour_11": "#ABCDEF", "colour_52": "#000000A0", "../escape": "#ABCDEF"}},
    {"values": {"colour_11": "#ABCDEF00", "colour_52": "#000000A0"}},
    {"values": {"colour_11": "#ABCDEF", "colour_52": "#000000"}},
    {"values": {"colour_11": "#GGGGGG", "colour_52": "#000000A0"}},
    {"path": "../escape.json"},
])
def test_invalid_saves_preserve_previous_file_and_in_memory_values(store, mutation):
    store.register(json.dumps(DEFINITION))
    saved = store.save(json.dumps(VALUES))
    before = (store.root / "settings.json").read_bytes()
    with pytest.raises(ValueError):
        store.save(json.dumps(VALUES | mutation))
    assert (store.root / "settings.json").read_bytes() == before
    assert store.register(json.dumps(DEFINITION)) == saved


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


def test_flush_failure_before_publication_discards_temporary_file(store, monkeypatch):
    store.register(json.dumps(DEFINITION))
    saved = store.save(json.dumps(VALUES))
    before = (store.root / "settings.json").read_bytes()
    def fail(*args):
        raise OSError("owned flush failure")
    monkeypatch.setattr(settings.files.os, "fsync", fail)
    with pytest.raises(OSError, match="owned flush failure"):
        store.save(json.dumps({"schema": 1, "values": {"colour_11": "#FFFFFF", "colour_52": "#00000000"}}))
    assert (store.root / "settings.json").read_bytes() == before
    assert store.register(json.dumps(DEFINITION)) == saved
    assert list(store.root.iterdir()) == [store.root / "settings.json"]


def test_settings_directory_cannot_be_used_as_a_file(store):
    (store.root / "settings.json").mkdir()
    with pytest.raises((OSError, ValueError)):
        store.register(json.dumps(DEFINITION))
    assert store.definition is None


def test_source_ui_settings_requires_a_trusted_provider_before_guest_initialization():
    from launcher.mod_contract import ManifestError
    from launcher.source_mod import SourceMod
    from mod_runtime.source import SourceManifest, SourceSnapshot

    manifest = SourceManifest.parse(b'''schema = 2
id = "owned.theme"
version = "0.1.0"
api = "0.3.0"
runtime = "python"
entry = "mod"
capabilities = ["ui-settings"]
''')
    snapshot = SourceSnapshot(manifest, (("mod.py", "raise AssertionError('must not execute')"),), 0)
    cleaned = []
    with pytest.raises(ManifestError, match="trusted settings provider"):
        SourceMod(None, snapshot, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


def test_save_requires_registration_and_registered_fields_cannot_change(store):
    with pytest.raises(ValueError, match="Register"):
        store.save(json.dumps(VALUES))
    store.register(json.dumps(DEFINITION))
    with pytest.raises(ValueError, match="cannot change"):
        store.register(json.dumps(DEFINITION | {"schema": 2}))


@pytest.mark.parametrize("contents", [b"broken", b"\xff", b"x" * 4097])
def test_invalid_existing_settings_are_preserved_for_recovery(store, contents):
    path = store.root / "settings.json"
    path.write_bytes(contents)
    with pytest.raises((ValueError, UnicodeError)):
        store.register(json.dumps(DEFINITION))
    assert store.record("owned") is None
    assert path.read_bytes() == contents


@pytest.mark.parametrize("phase", ["register", "save"])
def test_hardlinked_settings_cannot_be_read_or_overwritten(store, tmp_path, phase):
    if phase == "save":
        store.register(json.dumps(DEFINITION))
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(VALUES))
    os.link(outside, store.root / "settings.json")
    before = outside.read_bytes()
    with pytest.raises(ValueError, match="unshared"):
        getattr(store, phase)(json.dumps(DEFINITION if phase == "register" else VALUES))
    assert outside.read_bytes() == before


@pytest.mark.parametrize("phase", ["register", "save"])
def test_replaced_mod_directory_is_rejected(store, phase):
    if phase == "save":
        store.register(json.dumps(DEFINITION))
    original = store.root.with_name("original mod")
    store.root.rename(original)
    store.root.mkdir()
    with pytest.raises(ValueError, match="changed after admission"):
        getattr(store, phase)(json.dumps(DEFINITION if phase == "register" else VALUES))
    assert not list(store.root.iterdir())


def test_linked_mod_directory_is_rejected(tmp_path):
    original = tmp_path / "original"
    original.mkdir()
    linked = tmp_path / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(original)],
                       check=True, capture_output=True)
    else:
        linked.symlink_to(original, target_is_directory=True)
    try:
        with pytest.raises((OSError, ValueError)):
            settings.UISettings(linked)
    finally:
        linked.rmdir() if os.name == "nt" else linked.unlink()


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


def test_tab_count_and_label_conflicts_are_bounded(tmp_path):
    registry = settings.SettingsRequests()
    for number in range(5):
        root = tmp_path / str(number)
        root.mkdir()
        registry.bind(str(number), root)
    registry.call("0", "register-settings", json.dumps(DEFINITION))
    with pytest.raises(ValueError, match="labels must be unique"):
        registry.call("1", "register-settings", json.dumps(DEFINITION))
    for number in range(1, 4):
        registry.call(str(number), "register-settings", json.dumps(DEFINITION | {"label": str(number)}))
    # Registering the same tab at the limit remains idempotent.
    registry.call("0", "register-settings", json.dumps(DEFINITION))
    with pytest.raises(ValueError, match="four"):
        registry.call("4", "register-settings", json.dumps(DEFINITION | {"label": "four"}))
    assert len(registry.records) == 4


def test_full_example_palette_fits_interface_and_retains_all_52_rows(store):
    palette = runpy.run_path(str(Path(__file__).resolve().parents[2] / "examples/theme_mod/src/palette.py"))
    definition = {"schema": palette["PALETTE_SCHEMA"], "label": "Theme", "fields": [
        {"id": f"colour_{number}", "label": f"Custom Theme Colour {number}",
         "color": "#" + "".join(f"{channel:02X}" for channel in default)}
        for number, default, _ in palette["COLOR_GROUPS"]
    ]}
    text = json.dumps(definition, separators=(",", ":"))
    assert len(text.encode()) <= settings.MAX_SETTINGS_BYTES
    saved = store.register(text)
    assert store.save(saved) == saved
    assert len(store.record("example.theme")["fields"]) == 52
    assert store.record("example.theme")["fields"][-1]["value"] == "#000000A0"


@pytest.mark.parametrize("failure", ["menu", "settings"])
@pytest.mark.parametrize("closing", [False, True])
def test_both_native_registries_are_cleaned_when_one_transport_fails(tmp_path, failure, closing):
    from launcher.pack_mods import PackMods

    calls = []
    def publish(area):
        def update(*args):
            calls.append(area)
            if area == failure:
                raise OSError("owned transport failure")
        return update
    scope = PackMods(tmp_path, lambda line: None, set_menu=publish("menu"), set_settings=publish("settings"))
    scope.applied_menu = scope.applied_settings = [{"owner": "owned.mod"}]
    with pytest.raises((OSError, RuntimeError)):
        scope.close() if closing else scope._remove_menu("owned.mod")
    assert sorted(calls) == ["menu", "settings"]
