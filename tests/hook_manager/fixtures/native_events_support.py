# SPDX-License-Identifier: MPL-2.0
"""Shared loading and navigation fixture for event and lifecycle tests."""

import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.adapter_modules import AdapterModules
from hook_manager.manager import Manager
from hook_manager.menu import MenuAdapter
from hook_manager.native_events import NativeEvents
from hook_manager.native_readiness import NativeReadiness
from hook_manager.pack_discovery import PackDiscovery
from hook_manager.settings import SettingsAdapter
from hook_manager.themes import ThemeAdapter
from tests.hook_manager.fixtures.collected_activity import CollectedActivity


@pytest.fixture
def native_events(native_menu, monkeypatch, tmp_path):
    hub, _ = native_menu
    class GameState:
        def __init__(self, data_dir, save_path=None):
            self.path = data_dir
            self.data = json.loads(data_dir.read_text()) if data_dir else {}
        def load_season(self):
            if getattr(self, "fail_load", False):
                self.last_load_error = "Owned native load refused " + "x" * 5000 + " FULL NATIVE END"
                return False
            self.data = json.loads(self.path.read_text())
            return True
        def save_season(self, *, manual=False):
            return None
        def advance_week(self, off_week=False, *, persist=True):
            return True
        def finalize_and_rollover_season(self):
            return None
    class ScreenManager:
        def switch_to(self, screen):
            self.current = screen
            screen.manager = self
    class SessionPreloader:
        def __init__(self, manager, state, fonts=None, status=None):
            self.manager, self.state, self.status = manager, state, status
            self.done = self.cancelled = False
        def _report(self, label):
            if self.status is not None:
                self.status(label, 1, 1)
        def cancel(self):
            self.cancelled = True
    def start(self):
        self.is_loading = True
        self.state = GameState(self.path)
        return self.state
    def create(self):
        self.is_loading = False
        return "native new career"
    hub.HubScreen._start_continue_loading = start
    hub.HubScreen._start_new_season_loading = create
    class LoadGameScreen(hub.HubScreen):
        _load_selected = start
    class TeamSelectScreen(hub.HubScreen):
        _select_current = start
    for name, owner, value in (("game_state", "GameState", GameState), ("ui.screen_manager", "ScreenManager", ScreenManager),
                              ("ui.session_preload", "SessionPreloader", SessionPreloader),
                              ("ui.screens.load_game_screen", "LoadGameScreen", LoadGameScreen),
                              ("ui.screens.team_select_screen", "TeamSelectScreen", TeamSelectScreen)):
        module = ModuleType(name)
        setattr(module, owner, value)
        monkeypatch.setitem(sys.modules, name, module)
    host = SimpleNamespace(__name__="owned_event_scope", manager=Manager(), readiness=NativeReadiness())
    host.readiness.watch_career()
    host.activity = CollectedActivity(host.readiness)
    host.discovery = PackDiscovery(tmp_path)
    host.menu, host.themes = MenuAdapter(host.readiness.observe_screen), ThemeAdapter()
    host.settings = SettingsAdapter(host.themes)
    host.adapters = AdapterModules(host, {}, lambda record: host.activity.submit_log({**record, "event": "diagnostic"}))
    host.events = NativeEvents(host)
    host.events.start()
    manager = ScreenManager()
    screen = hub.HubScreen()
    manager.switch_to(screen)
    try:
        yield host, manager, screen, GameState, SessionPreloader
    finally:
        host.events.close()
        host.readiness.stop()
        host.manager.stop()
        host.activity.stop()
        assert host.activity.writer.finish(2)


def records(host):
    assert host.activity.writer.wait_idle(2)
    result = list(host.activity.records)
    host.activity.records.clear()
    return result


