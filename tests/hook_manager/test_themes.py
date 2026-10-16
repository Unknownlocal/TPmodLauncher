# SPDX-License-Identifier: MPL-2.0
"""Verify restart selection, coupled roles, aliases, collision safety and restoration."""

import json
from pathlib import Path
import runpy
import sys
from types import ModuleType

import pytest

from hook_manager.themes import ThemeAdapter, ThemeRequests, theme_definition
from hook_manager.settings import SettingsAdapter


PALETTE = Path(__file__).resolve().parents[2] / "examples/theme_mod/src/palette.py"
FIXTURE = Path(__file__).parent / "fixtures/native_themes_fixture.py"


def definition():
    return {"label": "Custom Theme", "colours": {
        role: "#" + "".join(f"{channel:02X}" for channel in colour)
        for _, colour, roles in runpy.run_path(str(PALETTE))["COLOR_GROUPS"] for role in roles
    }}


def record(owner="example.theme"):
    return {"owner": owner, **definition()}


@pytest.fixture
def native_themes(monkeypatch, tmp_path):
    path = tmp_path / "display_settings.json"
    path.write_text(json.dumps({"color_pack": "code-mod:example.theme"}))
    theme, packs = runpy.run_path(str(FIXTURE))["modules"](PALETTE, path)
    options = ModuleType("ui.screens.options_screen")
    options.COLOR_PACKS = packs.COLOR_PACKS
    options.WHITE = theme.UITheme.TEXT
    for module in (theme, packs, options):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    adapter = ThemeAdapter()
    yield theme, packs, options, adapter, path
    adapter.close()


def test_selected_palette_couples_roles_rebinds_aliases_and_avoids_double_mapping(native_themes):
    theme, packs, options, adapter, _ = native_themes
    original = theme.UITheme.PRIMARY
    data = record()
    # A chosen value equal to another source colour must not be translated twice.
    data["colours"]["PRIMARY"] = data["colours"]["SELECTION_BORDER"] = "#F0F0F0"
    data["colours"]["TEXT"] = data["colours"]["SELECTION_TEXT"] = "#102030"
    assert adapter.update([data])["state"] == "ready"
    assert options.COLOR_PACKS == ("blue", "emerald", "code-mod:example.theme")
    assert packs.COLOR_PACK_LABELS["code-mod:example.theme"] == "Custom Theme"
    assert theme.UITheme.PRIMARY == theme.UITheme.SELECTION_BORDER == (240, 240, 240)
    assert options.WHITE == (16, 32, 48)
    assert packs.CHROME[theme.UITheme.PRIMARY] == (240, 240, 240)
    assert packs.CHROME[original] == (240, 240, 240)
    assert packs.SESSION_CHROME[original] == original
    late = ModuleType("ui.late_screen")
    late.PRIMARY = theme.UITheme.PRIMARY
    sys.modules[late.__name__] = late
    adapter.close()
    del sys.modules[late.__name__]
    assert late.PRIMARY == original
    assert theme.UITheme.PRIMARY == original and options.WHITE == (240, 240, 240)
    assert options.COLOR_PACKS == packs.COLOR_PACKS == ("blue", "emerald")
    assert packs.CHROME[original] == original


def test_new_choices_and_saved_edits_wait_for_restart(native_themes):
    theme, packs, _, adapter, path = native_themes
    path.write_text('{"color_pack":"blue"}')
    adapter.update([record()])
    path.write_text('{"color_pack":"code-mod:example.theme"}')
    data = record()
    data["colours"]["BACKGROUND"] = "#ABCDEF"
    adapter.update([data])
    assert theme.UITheme.BACKGROUND == (5, 10, 19) and adapter.applied is None
    adapter.close()
    restarted = ThemeAdapter()
    try:
        restarted.update([data])
        assert theme.UITheme.BACKGROUND == (171, 205, 239)
        data["colours"]["BACKGROUND"] = "#445566"
        restarted.update([data])
        assert theme.UITheme.BACKGROUND == (171, 205, 239)
    finally:
        restarted.close()
    assert packs.COLOR_PACKS == ("blue", "emerald")


def test_later_registered_owner_can_supply_startup_selection_and_removal_restores(native_themes):
    theme, _, options, adapter, _ = native_themes
    other = record("other.theme")
    other["label"] = "Other"
    adapter.update([other])
    data = record()
    data["colours"]["BACKGROUND"] = "#123456"
    adapter.update([other, data])
    assert theme.UITheme.BACKGROUND == (18, 52, 86)
    adapter.update([other])
    assert theme.UITheme.BACKGROUND == (5, 10, 19)
    assert options.COLOR_PACKS == ("blue", "emerald", "code-mod:other.theme")
    adapter.update([])
    assert options.COLOR_PACKS == ("blue", "emerald")


def test_invalid_coupling_rolls_back_and_foreign_changes_survive_cleanup(native_themes):
    theme, packs, _, adapter, _ = native_themes
    data = record()
    data["colours"]["PRIMARY"] = "#ABCDEF"
    assert adapter.update([data])["state"] == "failed"
    assert packs.COLOR_PACKS == ("blue", "emerald")
    assert "code-mod:example.theme" not in packs.COLOR_PACK_LABELS
    assert theme.UITheme.PRIMARY == (48, 112, 185)
    adapter.update([record()])
    foreign = (1, 2, 3)
    theme.UITheme.PRIMARY = foreign
    adapter.close()
    assert theme.UITheme.PRIMARY is foreign


def test_registry_validates_size_roles_alpha_labels_and_copies():
    requests = ThemeRequests()
    data = definition()
    requests.register("example.theme", json.dumps(data))
    requests.records[0]["colours"].clear()
    assert len(requests.records[0]["colours"]) == 59
    for malformed in ({**data, "path": "ui.theme"}, {**data, "label": "line\nbreak"},
                      {**data, "colours": {**data["colours"], "OVERLAY": "#000000"}},
                      {**data, "colours": {"TEXT": "#123456"}}):
        with pytest.raises(ValueError):
            theme_definition(malformed)
    with pytest.raises(ValueError, match="4096"):
        requests.register("other", " " * 4097)
    with pytest.raises(ValueError, match="unique"):
        requests.register("other", json.dumps(data))
    with pytest.raises(ValueError, match="Duplicate"):
        requests.register("other", '{"label":"one","label":"two"}')


def test_removal_cancels_open_native_confirmation_and_clamps_cursor(native_themes, native_settings):
    _, packs, _, themes, _ = native_themes
    native_settings.COLOR_PACKS = packs.COLOR_PACKS
    settings = SettingsAdapter(themes)
    try:
        themes.update([record()])
        assert settings.update([])["state"] == "ready"  # Theme-only mods also get lifecycle handling.
        screen = native_settings.OptionsScreen()
        screen.draw(None)
        screen._color_pack_pending = "code-mod:example.theme"
        screen._color_pack_menu_cursor = 2
        screen._layout_cache = screen._chrome_cache = object()
        themes.update([])
        assert settings.update([])["state"] == "idle"
        assert screen._color_pack_pending is None and screen._color_pack_menu_cursor == 1
        assert screen._layout_cache is screen._chrome_cache is None
        assert native_settings.OptionsScreen.draw is native_settings.ORIGINAL_METHODS["draw"]
    finally:
        settings.close()


def test_cached_foreign_lookup_cannot_keep_palette_active_after_cleanup(native_themes):
    _, packs, _, adapter, _ = native_themes
    data = record()
    data["colours"]["BACKGROUND"] = "#ABCDEF"
    adapter.update([data])
    cached = packs.ChromePalette.__getitem__
    def foreign(palette, colour):
        return cached(palette, colour)
    packs.ChromePalette.__getitem__ = foreign
    adapter.close()
    assert packs.ChromePalette.__getitem__ is foreign
    assert packs.CHROME[(5, 10, 19)] == (5, 10, 19)


def test_startup_choice_survives_native_normalize_and_save_before_registration(native_themes):
    theme, packs, _, adapter, path = native_themes
    assert adapter.poll()["state"] == "idle"
    choice = json.loads(path.read_text())["color_pack"]
    # Native display startup normalizes against the labels, then immediately saves.
    normalized = choice if choice in packs.COLOR_PACK_LABELS else "blue"
    path.write_text(json.dumps({"color_pack": normalized}))
    assert normalized == "code-mod:example.theme"
    data = record()
    data["colours"]["BACKGROUND"] = "#ABCDEF"
    adapter.update([data])
    assert theme.UITheme.BACKGROUND == (171, 205, 239)
    assert packs.COLOR_PACK_LABELS[choice] == "Custom Theme"


def test_missing_startup_provider_falls_back_when_loading_finishes(native_themes, native_settings):
    _, packs, _, themes, path = native_themes
    native_settings.COLOR_PACKS = packs.COLOR_PACKS
    settings = SettingsAdapter(themes)
    try:
        themes.poll()
        settings.poll()
        screen = native_settings.OptionsScreen()
        screen.draw(None)
        screen._color_pack_pending = "code-mod:example.theme"
        screen._color_pack_menu_cursor = 2
        generation = settings.generation
        assert themes.finish_loading()
        settings.refresh()
        assert settings.generation == generation
        assert screen._color_pack_pending is None and screen._color_pack_menu_cursor == 1
        assert "code-mod:example.theme" not in packs.COLOR_PACK_LABELS
        assert json.loads(path.read_text())["color_pack"] == "code-mod:example.theme"
    finally:
        settings.close()
