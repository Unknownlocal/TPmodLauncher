# SPDX-License-Identifier: MPL-2.0
"""Check native preload progress and loading-attempt ownership without a game."""

import sys
from types import ModuleType

import pytest

from hook_manager.career_preload import CareerPreload
from hook_manager.manager import HookError


@pytest.fixture
def native(monkeypatch):
    helper, hub, game = (ModuleType(name) for name in
                        ("ui.session_preload", "ui.screens.hub_screen", "game_state"))
    class State:
        pass
    class Job:
        ready = False
        calls = 0
    class Screen:
        def switch_to(self, screen):
            self.current = screen
        def __init__(self):
            self.manager = self
            self.current = self
            self.state = State()
            self._preload_job = Job()
            self.status = []
        def _on_loading_status(self, *status):
            self.status.append(status)
    def advance_session_preload(screen, state):
        # The real helper attaches the career before its incremental work ends.
        screen.state = state
        screen._preload_job.calls += 1
        return screen._preload_job.ready
    helper.advance_session_preload = hub.advance_session_preload = advance_session_preload
    game.GameState = State
    navigation = ModuleType("ui.screen_manager")
    navigation.ScreenManager = Screen
    hub.HubScreen = type("HubScreen", (), {})
    monkeypatch.setitem(sys.modules, navigation.__name__, navigation)
    for module in (helper, hub, game):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    screen = Screen()
    yield helper, hub, game, screen, Screen, Job


def test_native_progress_wait_once_release_and_new_job_rejects_old_permission(native):
    helper, hub, _, screen, _, Job = native
    preload = CareerPreload()
    cached = hub.advance_session_preload
    try:
        state, job = screen.state, screen._preload_job
        assert not cached(screen, state) and job.calls == 1 and preload.loading_status() == {"state": "armed"}
        job.ready = True
        for _ in range(3):
            assert not cached(screen, state)
        generation = preload.loading_status()["generation"]
        assert job.calls == 4 and preload.generation == generation
        assert screen.status == [("Preparing regulation checks...", 0, 1)]
        assert preload.current(generation) is state
        preload.allow(state)
        assert cached(screen, state) is True
        screen._preload_job = Job()
        screen._preload_job.ready = True
        with pytest.raises(HookError, match="no longer current"):
            preload.current(generation)
        assert not cached(screen, state) and preload.generation > generation
        assert preload.loading_status()["state"] == "waiting"
        # A native cancellation invalidates the outstanding setup immediately.
        screen._shutting_down = True
        with pytest.raises(HookError, match="no longer current"):
            preload.current(preload.generation)
    finally:
        preload.stop()
    assert hub.advance_session_preload is helper.advance_session_preload
    assert cached(screen, screen.state) is True  # Cached stopped wrappers fall through.
    screen._shutting_down = False
    replacement = CareerPreload()
    try:
        assert not hub.advance_session_preload(screen, state)
        assert replacement.generation > preload.generation
        with pytest.raises(HookError, match="no longer current"):
            replacement.current(preload.generation)
    finally:
        replacement.stop()


def test_fixed_late_import_aliases_share_logic_and_preserve_foreign_replacement(native, monkeypatch):
    helper, hub, _, screen, Screen, _ = native
    preload = CareerPreload()
    original = helper.advance_session_preload
    try:
        for name in ("load_game_screen", "team_select_screen"):
            module = ModuleType("ui.screens." + name)
            module.advance_session_preload = original
            monkeypatch.setitem(sys.modules, module.__name__, module)
        preload.poll()
        assert all(row["state"] == "active" for row in preload.groups.values())
        screen._preload_job.ready = True
        load = sys.modules["ui.screens.load_game_screen"]
        assert not load.advance_session_preload(screen, screen.state)
        screen.manager.current = Screen()
        with pytest.raises(HookError, match="no longer current"):
            preload.current(preload.generation)
        screen.manager.current = screen
        foreign = lambda screen, state: True
        load.advance_session_preload = foreign
        with pytest.raises(HookError, match="no longer owns"):
            preload.allow(screen.state)
    finally:
        preload.stop()
    assert load.advance_session_preload is foreign and hub.advance_session_preload is original


def test_deferred_incompatible_alias_reports_unavailable_without_breaking_native_import(native, monkeypatch):
    helper, hub, _, _, _, _ = native
    preload = CareerPreload()
    module = ModuleType("ui.screens.load_game_screen")
    module.advance_session_preload = lambda changed: True
    monkeypatch.setitem(sys.modules, module.__name__, module)
    try:
        preload.poll()
        assert preload.loading_status()["state"] == "unavailable"
        assert "signature" in preload.loading_status()["detail"]
        assert module.advance_session_preload(None) is True
    finally:
        preload.stop()
    assert hub.advance_session_preload is helper.advance_session_preload


def test_missing_native_job_restores_completion_instead_of_raising_into_loader(native):
    helper, hub, _, screen, _, _ = native
    helper.advance_session_preload = hub.advance_session_preload = lambda screen, state: True
    preload = CareerPreload()
    screen._preload_job = None
    try:
        assert hub.advance_session_preload(screen, screen.state) is True
        assert preload.closed and preload.loading_status()["state"] == "unavailable"
        assert hub.advance_session_preload is helper.advance_session_preload
    finally:
        preload.stop()


def test_native_transitions_and_cancellation_retire_only_the_owned_career(native):
    _, hub, game, screen, Screen, Job = native
    departed = []
    preload = CareerPreload(lambda: departed.append(preload.generation))
    try:
        screen._preload_job.ready = True
        assert not hub.advance_session_preload(screen, screen.state)
        generation = preload.generation
        preload.allow(screen.state)
        dashboard = Screen()
        dashboard.state = screen.state
        screen.switch_to(dashboard)
        assert not departed  # Ordinary navigation within this career remains native.
        screen.switch_to(hub.HubScreen())
        assert departed == [generation] and preload.loading_status()["state"] == "left"
        with pytest.raises(HookError):
            preload.current(generation)
        # Reloading the same state/job is still a fresh attempt after returning.
        screen.switch_to(screen)
        assert not hub.advance_session_preload(screen, screen.state)
        assert preload.generation > generation
        screen._preload_job.cancelled = True
        preload.poll()
        assert len(departed) == 2 and preload.loading_status()["state"] == "left"
    finally:
        preload.stop()
