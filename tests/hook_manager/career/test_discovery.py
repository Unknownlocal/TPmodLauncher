# SPDX-License-Identifier: MPL-2.0
"""Read an owned main loop, including entry through Python's native C API."""

import ctypes
from functools import partial
import sys
import threading
from types import ModuleType, SimpleNamespace
import weakref

import pytest

from hook_manager.career_discovery import CareerDiscovery
from hook_manager.manager import HookError


@pytest.fixture
def native(monkeypatch):
    main, ui, game = (ModuleType(name) for name in ("__main__", "ui.screen_manager", "game_state"))
    ui.ScreenManager = type("ScreenManager", (), {})
    game.GameState = type("GameState", (), {})
    main.manager = ui.ScreenManager()
    main.manager.current = SimpleNamespace(state=None)
    exec("def _run_game(crash_reporter=None):\n    renamed_manager = manager\n    return callback()", vars(main))
    discovery = CareerDiscovery()
    main.callback = discovery.poll
    for module in (main, ui, game):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return main, ui, game, discovery


def test_career_changes_and_stale_setup_across_native_api_entry(native):
    main, _, game, discovery = native
    assert discovery.poll() == {"state": "pending", "generation": 0}
    assert main._run_game() == {"state": "no_career", "generation": 0}
    # PyRun_SimpleString gives discovery the same nested C-to-Python entry that
    # the bridge uses. All objects and the process belong to this test.
    api = ctypes.pythonapi.PyRun_SimpleString
    api.argtypes, api.restype = [ctypes.c_char_p], ctypes.c_int
    def through_c_api():
        assert api(b"capture = discovery.poll()") == 0
        return main.capture
    main.discovery, main.callback = discovery, through_c_api
    career = main.manager.current.state = game.GameState()
    reference = weakref.ref(career)
    assert main._run_game() == {"state": "loaded", "generation": 1}
    assert main._run_game() == {"state": "loaded", "generation": 1}
    main.manager.current.state = game.GameState()
    del career
    assert reference() is None  # Discovery must not retain an abandoned save.
    assert main._run_game() == {"state": "loaded", "generation": 2}
    main.callback = partial(discovery.current, 1)
    with pytest.raises(HookError, match="no longer current"):
        main._run_game()
    main.callback = partial(discovery.current, 2)
    assert main._run_game() is main.manager.current.state
    main.manager.current = SimpleNamespace(state=object())
    main.callback = discovery.poll
    assert main._run_game() == {"state": "no_career", "generation": 3}
    discovery.stop()
    assert discovery.poll() == {"state": "stopped", "generation": 3}


def test_body_and_local_renames_work_but_changed_signature_reports_failure(native):
    main, _, game, discovery = native
    main.manager.current.state = game.GameState()
    exec("def _run_game(crash_reporter=None):\n    different_name = manager\n    alias = different_name\n    extra = 42\n    return callback()", vars(main))
    assert main._run_game()["state"] == "loaded"
    exec("def _run_game(required_argument): pass", vars(main))
    original = main._run_game
    status = discovery.poll()
    assert status["state"] == "unavailable" and status["generation"] == 2
    assert "COMPATIBILITY FAILED" in status["detail"] and "continuing native game" in status["detail"]
    assert main._run_game is original
    with pytest.raises(HookError):
        discovery.current(1)


def test_ambiguous_native_manager_does_not_choose_a_career(native):
    main, ui, _, discovery = native
    main.other = ui.ScreenManager()
    exec("def _run_game(crash_reporter=None):\n    first, second = manager, other\n    return callback()", vars(main))
    status = main._run_game()
    assert status["state"] == "unavailable" and "more than one" in status["detail"]
    assert discovery.poll() == status  # Do not silently revive an unsupported mapping.


def test_import_wait_and_worker_rejection_do_not_change_discovery(native):
    main, ui, _, discovery = native
    ui.__spec__ = SimpleNamespace(_initializing=True)
    assert main._run_game() == {"state": "pending", "generation": 0}
    errors = []
    def worker():
        try:
            discovery.poll()
        except HookError as error:
            errors.append(str(error))
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(3)
    assert not thread.is_alive() and len(errors) == 1 and "main thread" in errors[0]
    assert discovery.status() == {"state": "pending", "generation": 0}
