# SPDX-License-Identifier: MPL-2.0
"""Verify owned settings tabs, editor input and acknowledged saves with synthetic UI."""

import sys
import threading
from types import SimpleNamespace

import pytest

from hook_manager.settings import SettingsAdapter, SettingsEditor, settings_records


def record(owner="owned.settings", label="Theme", count=2):
    return {"owner": owner, "label": label, "schema": 1, "fields": [
        {"id": f"colour_{n}", "label": f"Custom Theme Colour {n}", "type": "color",
         "default": "#112233" if n != 2 else "#000000A0", "value": "#112233" if n != 2 else "#000000A0"}
        for n in range(1, count + 1)
    ]}


def key(pg, value, mod=0):
    return SimpleNamespace(type=pg.KEYDOWN, key=value, mod=mod)


def open_editor(adapter, module, monkeypatch):
    monkeypatch.setattr(SettingsEditor, "draw", lambda *args: None)
    assert adapter.update([record(count=52)])["state"] == "ready"
    screen = module.OptionsScreen()
    screen.active_tab = adapter.tab_key("owned.settings")
    assert screen.draw("surface") == "native draw"
    return screen, screen._tpm_settings_editor


def test_tabs_coexist_with_native_options_and_owned_cleanup(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    assert module.OPTION_TABS == [("game", "Game"), ("graphics", "Graphics"), ("code-mod:owned.settings", "Theme")]
    assert screen._layout_cache is screen._chrome_cache is None
    cached = screen.handle_event
    assert screen.handle_event(key(module.pygame, module.pygame.K_TAB)) is None
    assert editor.focus == 0 and module.input_calls == ["start"]
    screen._layout_cache = screen._chrome_cache = object()
    adapter.close()
    assert screen._layout_cache is screen._chrome_cache is None
    assert module.OPTION_TABS == [("game", "Game"), ("graphics", "Graphics")]
    assert screen.active_tab == "game" and module.input_calls[-1] == "stop"
    assert module.OptionsScreen.draw is module.ORIGINAL_METHODS["draw"]
    assert cached(key(module.pygame, module.pygame.K_ESCAPE)) == "native event"


def test_hex_editing_validation_alpha_reset_and_save_ack(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    pg = module.pygame
    screen.handle_event(key(pg, pg.K_TAB))
    screen.handle_event(SimpleNamespace(type=pg.TEXTINPUT, text="#abcdef"))
    assert editor.drafts[editor.owner]["colour_1"] == "#ABCDEF"
    screen.handle_event(key(pg, pg.K_RETURN))
    pending = adapter.status()["save"]
    assert pending["values"]["colour_1"] == "#ABCDEF" and pending["id"] == 1
    # A pending save blocks further edits and cannot be queued twice.
    screen.handle_event(SimpleNamespace(type=pg.TEXTINPUT, text="bad"))
    screen.handle_event(key(pg, pg.K_RETURN))
    assert adapter.status()["save"] == pending
    updated = record(count=52)
    updated["fields"][0]["value"] = "#ABCDEF"
    adapter.update([updated], {"id": 1, "owner": pending["owner"], "success": True})
    screen.draw("surface")
    assert adapter.pending is None and adapter.messages[editor.owner] == "Settings saved."
    editor.focus_row(1, screen, pg)
    screen.handle_event(SimpleNamespace(type=pg.TEXTINPUT, text="#123456"))
    assert not editor.valid()  # Alpha field requires eight digits.
    screen.handle_event(key(pg, pg.K_RETURN))
    assert adapter.pending is None
    screen.handle_event(key(pg, pg.K_a, pg.KMOD_CTRL))
    screen.handle_event(SimpleNamespace(type=pg.TEXTINPUT, text="#12345680"))
    assert editor.valid()
    screen.handle_event(key(pg, pg.K_r, pg.KMOD_CTRL))
    assert editor.drafts[editor.owner]["colour_1"] == "#112233"
    assert editor.drafts[editor.owner]["colour_2"] == "#000000A0"
    assert adapter.pending is None  # Reset is staged until Save.


@pytest.mark.parametrize("index,text,expected", [
    (0, "abcdef", "#ABCDEF"), (0, "#abcdef", "#ABCDEF"),
    (1, "12345680", "#12345680"), (1, "#12345680", "#12345680"),
    (0, "12345680", None), (1, "123456", None), (0, "##123456", None), (0, "12GG56", None),
])
def test_hex_input_optional_prefix_retains_rgb_and_alpha_validation(
    native_settings, monkeypatch, index, text, expected,
):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    editor.focus_row(index, screen, module.pygame)
    screen.handle_event(SimpleNamespace(type=module.pygame.TEXTINPUT, text=text))
    assert editor.valid() == (expected is not None)
    screen.handle_event(key(module.pygame, module.pygame.K_RETURN))
    if expected is None:
        assert adapter.pending is None
    else:
        assert adapter.pending["values"][f"colour_{index + 1}"] == expected


def test_saved_unprefixed_draft_does_not_hide_later_committed_values(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    screen.handle_event(key(module.pygame, module.pygame.K_TAB))
    screen.handle_event(SimpleNamespace(type=module.pygame.TEXTINPUT, text="ABCDEF"))
    editor.save()
    updated = record(count=52)
    updated["fields"][0]["value"] = "#ABCDEF"
    adapter.update([updated], {"id": 1, "owner": editor.owner, "success": True})
    screen.draw(None)
    updated["fields"][0]["value"] = "#445566"
    adapter.update([updated])
    screen.draw(None)
    assert editor.drafts[editor.owner]["colour_1"] == "#445566"


def test_selection_arrows_collapse_to_boundaries_and_edits_clear_saved_status(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    pg = module.pygame
    screen.handle_event(key(pg, pg.K_TAB))
    screen.handle_event(key(pg, pg.K_LEFT))
    assert editor.cursor == 0 and not editor.select_all
    screen.handle_event(key(pg, pg.K_a, pg.KMOD_CTRL))
    screen.handle_event(key(pg, pg.K_RIGHT))
    assert editor.cursor == 7 and not editor.select_all
    adapter.messages[editor.owner] = "Settings saved."
    screen.handle_event(key(pg, pg.K_DELETE))  # No change at the end of the value.
    assert adapter.messages[editor.owner] == "Settings saved."
    screen.handle_event(key(pg, pg.K_BACKSPACE))
    assert editor.owner not in adapter.messages
    adapter.messages[editor.owner] = "Settings saved."
    screen.handle_event(SimpleNamespace(type=pg.TEXTINPUT, text="3"))
    assert editor.owner not in adapter.messages
    assert editor.drafts[editor.owner]["colour_1"] == "#112233"


def test_keyboard_navigation_scrolls_to_last_field_and_pointer_wheel_scrolls(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    pg = module.pygame
    screen.handle_event(key(pg, pg.K_TAB, pg.KMOD_SHIFT))
    assert editor.focus == 51 and editor.offsets[editor.owner] > 0
    screen.handle_event(SimpleNamespace(type=pg.MOUSEWHEEL, y=100))
    assert editor.offsets[editor.owner] == 0
    screen.handle_event(key(pg, pg.K_ESCAPE))
    assert editor.focus is None
    assert screen.handle_event(key(pg, pg.K_ESCAPE)) == "native event"


def test_pointer_focus_reset_and_save_use_canvas_coordinates(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    pg = module.pygame
    monkeypatch.setattr(screen, "window_to_canvas", lambda pos: (pos[0] // 2, pos[1] // 2))
    body, viewport, _, _, _, save, reset = editor.layout(screen, pg)
    def click(pos):
        screen.handle_event(SimpleNamespace(type=pg.MOUSEBUTTONDOWN, button=1, pos=(pos[0] * 2, pos[1] * 2)))
    click((viewport.x + 10, viewport.y + 10))
    assert editor.focus == 0
    screen.handle_event(SimpleNamespace(type=pg.TEXTINPUT, text="#ABCDEF"))
    click(reset.center)
    assert editor.drafts[editor.owner]["colour_1"] == "#112233"
    click(save.center)
    assert adapter.pending["values"]["colour_1"] == "#112233"


def test_failed_save_ack_preserves_drafts_and_unrelated_registry_updates(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    screen, editor = open_editor(adapter, module, monkeypatch)
    editor.drafts[editor.owner]["colour_1"] = "445566"
    editor.save()
    adapter.update([record(count=52), record("owned.second", "Other")],
                   {"id": 1, "owner": "owned.settings", "success": False})
    screen.draw("surface")
    assert editor.drafts[editor.owner]["colour_1"] == "445566"
    assert adapter.pending is None
    assert "Try again" in adapter.messages[editor.owner]


def test_deferred_import_conflicts_and_foreign_methods_are_not_overwritten(native_settings, monkeypatch):
    module, adapter = native_settings, SettingsAdapter()
    monkeypatch.delitem(sys.modules, module.__name__)
    assert adapter.update([record()])["state"] == "pending"
    monkeypatch.setitem(sys.modules, module.__name__, module)
    module.__spec__._initializing = True
    assert adapter.poll()["state"] == "pending"
    assert adapter.poll(module)["state"] == "ready"
    def foreign(self, event):
        return "foreign"
    module.OptionsScreen.handle_event = foreign
    with pytest.raises(RuntimeError, match="another owner"):
        adapter.close()
    assert module.OptionsScreen.handle_event is foreign
    assert module.OPTION_TABS == [("game", "Game"), ("graphics", "Graphics")]
    adapter.close()
    module.__spec__._initializing = False
    assert adapter.update([record(label="Game")])["state"] == "failed"


@pytest.mark.parametrize("mutation", [
    {"owner": "../escape"}, {"schema": True}, {"label": "line\nbreak"}, {"fields": []},
    {"fields": [{"id": "field", "label": "Field", "type": "callback", "default": "#000000", "value": "#000000"}]},
    {"fields": [{"id": "field", "label": "Field", "type": "color", "default": "#000000", "value": "#00000000"}]},
])
def test_native_declarations_reject_invalid_or_executable_data(mutation):
    with pytest.raises(ValueError):
        settings_records([record() | mutation])


def test_declaration_and_status_copies_cannot_mutate_owned_state():
    adapter = SettingsAdapter()
    original = record()
    adapter.update([original])
    original["fields"][0]["value"] = "changed"
    adapter.queue_save("owned.settings", {"schema": 1, "values": {"colour_1": "#112233", "colour_2": "#000000A0"}})
    status = adapter.status()
    status["save"]["values"].clear()
    assert len(adapter.status()["save"]["values"]) == 2


def test_worker_mutations_are_rejected():
    errors = []
    def worker():
        try:
            SettingsAdapter().update([])
        except RuntimeError as error:
            errors.append(str(error))
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)
    assert errors == ["Settings changes require the game main thread"]
