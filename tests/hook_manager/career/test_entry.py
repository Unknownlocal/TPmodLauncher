# SPDX-License-Identifier: MPL-2.0
"""Check the private career wait boundary without starting or attaching a game."""

import sys
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from hook_manager.career_entry import CareerEntryGate
from hook_manager.manager import HookError


@pytest.fixture
def native(monkeypatch):
    ui, game = ModuleType("ui.screen_manager"), ModuleType("game_state")
    calls = []
    class ScreenManager:
        def handle_event(self, event):
            calls.append(event)
            return "event"
        def update(self):
            calls.append("update")
            return "update"
    ui.ScreenManager, game.GameState = ScreenManager, type("GameState", (), {})
    for module in (ui, game):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    manager = ScreenManager()
    manager.current = SimpleNamespace(state=None)
    discovery = SimpleNamespace(_screen_manager=lambda: manager)
    return ui, game, manager, discovery, calls


def test_career_entry_wait_release_replacement_and_owned_cleanup(native):
    ui, game, manager, discovery, calls = native
    original = ui.ScreenManager.update
    gate = CareerEntryGate(discovery)
    cached = manager.update
    try:
        assert manager.handle_event("menu") == "event" and manager.update() == "update"
        career = manager.current.state = game.GameState()
        reference = weakref.ref(career)
        assert manager.handle_event("sign") is None and manager.update() is None
        assert calls == ["menu", "update"]
        with pytest.raises(TypeError):
            manager.update("unexpected")
        gate.allow(career)
        assert manager.handle_event("sign") == "event" and manager.update() == "update"
        other = ui.ScreenManager()
        other.current = SimpleNamespace(state=game.GameState())
        assert other.update() == "update"  # Only the observed native manager is held.
        manager.current.state = game.GameState()
        del career
        assert reference() is None  # Permission must not keep an abandoned save alive.
        assert manager.update() is None
        with pytest.raises(HookError, match="displayed career"):
            gate.allow(game.GameState())
        manager.current.state = None
        assert manager.update() == "update"
    finally:
        gate.stop()
    assert ui.ScreenManager.update is original and cached() == "update"


def test_career_entry_signature_failure_is_atomic_and_foreign_wrappers_survive(native):
    ui, game, manager, discovery, _ = native
    original = ui.ScreenManager.handle_event
    ui.ScreenManager.update = lambda self, changed: None
    with pytest.raises(HookError, match="signature"):
        CareerEntryGate(discovery)
    assert ui.ScreenManager.handle_event is original
    ui.ScreenManager.update = lambda self: None
    gate = CareerEntryGate(discovery)
    replacement = lambda self: "foreign"
    ui.ScreenManager.update = replacement
    manager.current.state = game.GameState()
    try:
        with pytest.raises(HookError, match="displayed career"):
            gate.allow(manager.current.state)
    finally:
        gate.stop()
    assert ui.ScreenManager.update is replacement and ui.ScreenManager.handle_event is original
