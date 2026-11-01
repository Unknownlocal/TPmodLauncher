# SPDX-License-Identifier: MPL-2.0
"""Exercise real loading reads, worker imports and user changes without status collection."""

import importlib
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.adapter_modules import AdapterModules
from hook_manager.callables import CallableAdapters
from hook_manager.manager import Manager
from hook_manager.menu import MenuAdapter
from hook_manager.native_activity import NativeActivity
from hook_manager.native_events import NativeEvents
from hook_manager.native_readiness import NativeReadiness
from hook_manager.pack_discovery import PackDiscovery
from hook_manager.settings import SettingsAdapter
from hook_manager.themes import ThemeAdapter


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
    host.activity = NativeActivity(host.readiness)
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


def test_early_save_read_failure_keeps_stage_and_original_exception(native_events, tmp_path):
    host, _, screen, _, _ = native_events
    screen.path = tmp_path / "owned-save.json"
    screen.path.write_text('{"broken":')
    with pytest.raises(json.JSONDecodeError) as failure:
        screen._start_continue_loading()
    assert failure.value.launcher_activity["kind"] == "save_loading"
    assert not hasattr(screen, "_preload_job")
    failures = [row for row in records(host) if row.get("level") == "ERROR"]
    assert len(failures) == 1 and "JSONDecodeError" in failures[0]["traceback"]
    assert failures[0]["activity"]["kind"] == "save_loading"


def test_cancelled_old_preload_cannot_clear_replacement_then_navigation_restores_career(native_events, tmp_path):
    host, manager, screen, State, Job = native_events
    screen.path = tmp_path / "owned-save.json"
    screen.path.write_text('{"week": 17}')
    assert screen._start_continue_loading().data == {"week": 17}
    old = Job(manager, screen.state)
    screen._preload_job = old
    assert screen._start_continue_loading().data == {"week": 17}
    current = Job(manager, screen.state)
    screen._preload_job = current
    old.cancel()
    assert host.readiness.current_activity()["kind"] == "save_loading"
    current.done = True
    current._report("")
    career = type("CareerScreen", (), {})()
    career.state = screen.state
    manager.switch_to(career)
    changes = [row["status"] for row in records(host) if row.get("event") == "readiness"]
    assert changes[-1]["activity"]["kind"] == "in_save" and changes[-1]["career"] is not None
    assert host.readiness.current_activity()["kind"] == "in_save"


def test_settings_save_is_published_once_and_previous_payload_is_immutable(native_events):
    host, _, _, _, _ = native_events
    row = {"owner": "owned.settings", "label": "Owned Settings", "schema": 1,
           "fields": [{"id": "accent", "label": "Accent", "type": "color", "default": "#112233", "value": "#112233"}]}
    host.settings.update([row])
    host.events.refresh()
    records(host)
    value = {"schema": 1, "values": {"accent": "#334455"}}
    host.settings.queue_save("owned.settings", value)
    host.settings.queue_save("owned.settings", value)
    changed = [record for record in records(host) if record.get("event") == "settings"]
    assert len(changed) == 1 and changed[0]["status"]["save"]["values"] == value["values"]
    request = host.settings.pending
    host.settings.update([row], {"id": request["id"], "owner": request["owner"], "success": True})
    host.events.refresh()
    assert host.events.status()["settings"]["save"] is None
    assert changed[0]["status"]["save"]["values"] == value["values"]


def test_worker_import_only_installs_after_its_completion_event(native_events, tmp_path, monkeypatch):
    host, _, _, _, _ = native_events
    path = tmp_path / "owned_import_event.py"
    path.write_text("READY = 'native module'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    host.readiness.configure({"career": False, "modules": ["owned_import_event"]})
    records(host)
    worker = threading.Thread(target=lambda: importlib.import_module("owned_import_event"))
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    try:
        before = records(host)
        assert any(record.get("event") == "native-service" for record in before)
        assert "owned_import_event" not in host.events.status()["readiness"]["modules"]
        host.events.service()  # The launcher's one requested main-thread entry.
        after = records(host)
        assert any("owned_import_event" in row["status"]["modules"] for row in after if row.get("event") == "readiness")
    finally:
        sys.modules.pop("owned_import_event", None)


@pytest.mark.parametrize("failure", ["return_false", "constructor"])
def test_failed_native_worker_returns_to_menu_without_a_preload_job(native_events, tmp_path, failure):
    host, _, screen, State, _ = native_events
    screen.path = tmp_path / "failed-save.json"
    screen.path.write_text('{"week": 7}')
    state = screen._start_continue_loading()
    state.fail_load = True
    outcomes = []
    if failure == "constructor":
        screen.path.write_text('{"broken":')
    def load():
        try:
            outcomes.append(State(screen.path) if failure == "constructor" else state.load_season())
        except json.JSONDecodeError as error:
            outcomes.append(error)
        finally:
            screen.is_loading = False
    screen._load_thread = threading.Thread(target=load)
    screen._load_thread.start()
    screen._load_thread.join(2)
    assert not hasattr(screen, "_preload_job")
    host.events.service()
    result = records(host)
    assert not any(row.get("fatal") for row in result)
    assert host.events.status()["readiness"]["activity"]["kind"] == "menu"
    if failure == "return_false":
        assert outcomes == [False]
        record = next(row for row in result if row.get("detail") == "Native save load failed")
        assert record["traceback"].endswith("FULL NATIVE END") and "Traceback" not in record["traceback"]
    else:
        assert isinstance(outcomes[0], json.JSONDecodeError)
        record = next(row for row in result if "JSONDecodeError" in row.get("traceback", ""))
    assert record["activity"]["kind"] == "save_loading"


def test_inflight_import_gets_completion_without_a_wrapped_loader(native_events, tmp_path, monkeypatch):
    host, _, _, _, _ = native_events
    gate = ModuleType("owned_gate")
    gate.entered, gate.release = threading.Event(), threading.Event()
    monkeypatch.setitem(sys.modules, gate.__name__, gate)
    path = tmp_path / "owned_inflight.py"
    path.write_text("from owned_gate import entered, release\nentered.set()\nrelease.wait()\nREADY = 'complete'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    worker = threading.Thread(target=lambda: importlib.import_module("owned_inflight"))
    worker.start()
    completed = threading.Event()
    imported = host.events.imported
    def wake(manager, name=None):
        imported(manager, name)
        completed.set()
    monkeypatch.setattr(host.events, "imported", wake)
    try:
        assert gate.entered.wait(2)
        host.readiness.configure({"career": False, "modules": ["owned_inflight"]})
        assert "owned_inflight" not in host.events.status()["readiness"]["modules"]
        gate.release.set()
        worker.join(2)
        assert completed.wait(2)
        host.events.service()
        assert "owned_inflight" in host.events.status()["readiness"]["modules"]
        assert sys.modules["owned_inflight"].READY == "complete"
    finally:
        gate.release.set()
        worker.join(2)
        sys.modules.pop("owned_inflight", None)


def test_active_validation_completion_resumes_deferred_import(native_events, tmp_path, monkeypatch):
    from hook_manager.callables import CallableAdapters, CallableTarget

    host, _, _, _, _ = native_events
    path = tmp_path / "owned_busy.py"
    path.write_text("def value(number):\n    return number + 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    guards = CallableAdapters()
    guards.approve(CallableTarget("owned.busy", "owned_busy", (), "value", "(number)"))
    Manager.install(guards, "owned.busy", [{"target": "owned.busy", "callback": "internal", "phase": "before"}])
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    def work():
        with guards.operation():
            importlib.import_module("owned_busy")
            entered.set()
            release.wait(2)
    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert entered.wait(2)
        host.events.service()
        assert guards.groups["owned.busy"]["state"] == "pending"
        imported = host.events.imported
        def wake(manager):
            imported(manager)
            completed.set()
        monkeypatch.setattr(host.events, "imported", wake)
        release.set()
        worker.join(2)
        assert completed.wait(2)
        host.events.service()
        assert guards.groups["owned.busy"]["state"] == "active"
        assert sys.modules["owned_busy"].value(4) == 5
    finally:
        release.set()
        worker.join(2)
        guards.stop()
        sys.modules.pop("owned_busy", None)


def test_cleanup_waits_for_lock_release_after_last_operation_finished(native_events, monkeypatch):
    host, _, _, _, _ = native_events
    guards = CallableAdapters()
    host.adapters.regulations.append(guards)
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    received = []
    def control_ready(request, dependency):
        received.append((request, dependency))
        completed.set()
    monkeypatch.setattr(host.events, "control_ready", control_ready)
    def finishing_worker():
        # Match nested registry use: the operation has ended but its final
        # lock owner has not yet left. There is no future counter decrement.
        with guards._lock:
            with guards.operation():
                pass
            entered.set()
            assert release.wait(3)
    worker = threading.Thread(target=finishing_worker)
    worker.start()
    try:
        assert entered.wait(2)
        host.events.control_id = "a" * 32
        assert not host.adapters.close()
        host.events.control_id = "b" * 32  # Replace a cancelled request on the same lock.
        assert not host.adapters.close() and not completed.is_set()
        release.set()
        worker.join(2)
        assert completed.wait(2) and received == [("b" * 32, "operations")]
        assert host.adapters.close() and guards.closed
    finally:
        release.set()
        worker.join(2)


def test_generic_worker_error_stays_diagnostic_and_original_hook_runs(monkeypatch):
    delegated = []
    monkeypatch.setattr(threading, "excepthook", lambda args: delegated.append(args.exc_value))
    readiness = NativeReadiness()
    readiness._activity_status = {"kind": "in_save", "generation": 1, "sequence": 1}
    activity = NativeActivity(readiness)
    try:
        def fail():
            raise RuntimeError("Owned unrelated worker error FULL END")
        worker = threading.Thread(target=fail)
        worker.start()
        worker.join(2)
        assert activity.writer.wait_idle(2)
        result = list(activity.records)
        assert len(delegated) == 1 and not any(row.get("fatal") for row in result)
        assert any(row.get("activity", {}).get("kind") == "in_save" and "FULL END" in row.get("traceback", "") for row in result)
    finally:
        activity.stop()
        assert activity.writer.finish(2)


def test_native_crash_recorder_and_python_hook_keep_one_generic_fatal(native_events, monkeypatch):
    host, manager, _, State, _ = native_events
    module = ModuleType("runtime_crash_reporter")
    class RuntimeCrashReporter:
        def __init__(self):
            self.saved = []
        def record_exception(self, exc_info) -> "None":
            self.saved.append(exc_info[1])
    module.RuntimeCrashReporter = RuntimeCrashReporter
    monkeypatch.setitem(sys.modules, module.__name__, module)
    host.events.service()
    career = type("CareerScreen", (), {})()
    career.state = State(None)
    manager.switch_to(career)
    delegated = []
    recorder = RuntimeCrashReporter()
    with monkeypatch.context() as hooks:
        hooks.setattr(host.activity, "_exception_hook", lambda kind, error, tb: delegated.append(error))
        try:
            raise ValueError("Owned generic fatal FULL END")
        except ValueError as error:
            error.add_note("Original native crash note")
            info = sys.exc_info()
            recorder.record_exception(info)
            recorder.record_exception(info)
            sys.excepthook(*info)
            assert recorder.saved == [error, error] and delegated == [error]
    result = records(host)
    failures = [row for row in result if row.get("fatal")]
    assert len(failures) == 1 and failures[0]["activity"]["kind"] == "in_save"
    assert "Original native crash note" in failures[0]["traceback"] and "FULL END" in failures[0]["reason"]
