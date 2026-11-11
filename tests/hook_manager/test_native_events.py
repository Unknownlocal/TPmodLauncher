# SPDX-License-Identifier: MPL-2.0
"""Exercise real loading reads, worker imports and user changes without status collection."""

import importlib
import json
import sys
import threading
from types import ModuleType

import pytest

from hook_manager.callables import CallableAdapters
from hook_manager.manager import Manager
from hook_manager.native_readiness import NativeReadiness
from tests.hook_manager.fixtures.collected_activity import CollectedActivity
from tests.hook_manager.fixtures.native_events_support import native_events, records


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
    unrelated = Manager()
    def unexpected_poll():
        raise AssertionError("An unrelated manager was serviced")
    monkeypatch.setattr(unrelated, "poll", unexpected_poll)
    monkeypatch.setattr(host.activity, "poll", unexpected_poll)
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
        unrelated.stop()
        sys.modules.pop("owned_import_event", None)


def test_nested_control_changes_publish_one_final_snapshot_and_status_does_not_collect(native_events, monkeypatch):
    host, _, _, _, _ = native_events
    records(host)
    calls = []
    # Count real state collection while keeping its behaviour and payloads.
    for owner, name in ((host.discovery, "poll"), (host.readiness, "snapshot"),
                        (host.activity, "snapshot"), (host.adapters, "status")):
        original = getattr(owner, name)
        def collect(*args, _original=original, _name=name, _owner=owner, **kwargs):
            calls.append((_owner, _name))
            return _original(*args, **kwargs)
        monkeypatch.setattr(owner, name, collect)
    row = {"owner": "owned.batched", "label": "Batched Settings", "schema": 1,
           "fields": [{"id": "accent", "label": "Accent", "type": "color", "default": "#112233", "value": "#112233"}]}
    with host.events.batch():
        host.settings.update([row])
        host.events.refresh()
        with host.events.batch():
            host.settings.queue_save("owned.batched", {"schema": 1, "values": {"accent": "#334455"}})
            host.menu.set_loading(dict(state="complete", loaded=1, failed=0, total=1))
            host.events.refresh()
        assert not calls
        host.events.service()
    assert len(calls) == 4 and len(set((id(owner), name) for owner, name in calls)) == 4
    status = host.events.status()
    assert status["menu"]["loading"]["state"] == "complete"
    assert status["settings"]["save"]["values"] == {"accent": "#334455"}
    assert host.events.status() == status and len(calls) == 4
    updates = records(host)
    assert len([record for record in updates if record.get("event") == "settings"]) == 1
    assert len([record for record in updates if record.get("event") == "menu"]) == 1


def test_binding_during_worker_import_keeps_completion_and_its_service_wake(native_events, tmp_path, monkeypatch):
    host, _, _, _, _ = native_events
    name = "owned_binding_race"
    (tmp_path / (name + ".py")).write_text("VALUE = 17\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    entered, release, binding, bound = (threading.Event() for _ in range(4))
    watcher, observed, created, errors = Manager(), [], [], []
    watcher.observe(name, lambda module: observed.append(module.VALUE) or True)
    class PendingNames(set):
        def __contains__(self, value):
            if value == name and threading.current_thread() is not threading.main_thread():
                entered.set()
                assert release.wait(2)
            return super().__contains__(value)
    watcher.wanted = PendingNames(watcher.wanted)
    bind = host.events.bind
    def bind_during_import(manager):
        binding.set()
        bind(manager)
        bound.set()
    monkeypatch.setattr(host.events, "bind", bind_during_import)
    def importing():
        try:
            importlib.import_module(name)
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=importing)
    binder = threading.Thread(target=lambda: created.append(Manager()))
    records(host)
    worker.start()
    try:
        assert entered.wait(1)
        binder.start()
        assert binding.wait(1)
        # Permit a concurrent bind to finish if the registry uses a safe
        # snapshot; a lock-based implementation may wait until release.
        bound.wait(.05)
        release.set()
        worker.join(2)
        binder.join(2)
        assert not errors and not worker.is_alive() and not binder.is_alive()
        assert any(row.get("event") == "native-service" for row in records(host))
        host.events.service()
        assert observed == [17]
    finally:
        release.set()
        worker.join(2)
        if binder.ident is not None:
            binder.join(2)
        watcher.stop()
        for manager in created:
            manager.stop()
        sys.modules.pop(name, None)


def test_worker_loading_completion_during_collection_keeps_next_service_wake(native_events, tmp_path, monkeypatch):
    host, _, screen, _, _ = native_events
    screen.path = tmp_path / "owned-worker-save.json"
    screen.path.write_text('{"week": 3}')
    screen._start_continue_loading()
    attempt = host.readiness._loading_attempt
    records(host)
    collecting, completed = threading.Event(), threading.Event()
    original = host.discovery.poll
    def collection_in_progress():
        collecting.set()
        assert completed.wait(2)
        return original()
    monkeypatch.setattr(host.discovery, "poll", collection_in_progress)
    def finish_loading():
        assert collecting.wait(2)
        host.readiness.end_loading(attempt)
        completed.set()
    worker = threading.Thread(target=finish_loading)
    worker.start()
    try:
        host.events.refresh()
        worker.join(2)
        assert not worker.is_alive()
        assert host.events.status()["readiness"]["activity"]["kind"] == "save_loading"
        assert any(row.get("event") == "native-service" for row in records(host))
        host.events.service()
        assert host.events.status()["readiness"]["activity"]["kind"] == "menu"
    finally:
        collecting.set()
        worker.join(2)


def test_cancelled_regulation_preload_publishes_departure_without_polling_on_unrelated_refresh(native_events, monkeypatch):
    from hook_manager.manager import HookError

    host, manager, screen, State, Job = native_events
    def advance(screen, state):
        return True
    for name in ("ui.session_preload", "ui.screens.hub_screen", "ui.screens.load_game_screen", "ui.screens.team_select_screen"):
        monkeypatch.setattr(sys.modules[name], "advance_session_preload", advance, raising=False)
    host.adapters.await_regulations()
    screen.state = State(None)
    screen._preload_job = Job(manager, screen.state)
    screen._preload_job.done = True
    assert not sys.modules["ui.screens.hub_screen"].advance_session_preload(screen, screen.state)
    preload = host.adapters.preload
    generation = preload.generation
    records(host)
    calls, poll = [], preload.poll
    def observed_poll():
        calls.append(True)
        return poll()
    monkeypatch.setattr(preload, "poll", observed_poll)
    screen._preload_job.cancel()
    assert host.events.status()["adapters"]["career_preload"] == {"state": "left", "generation": generation}
    with pytest.raises(HookError):
        preload.current(generation)
    assert len(calls) == 1
    host.events.refresh()
    assert len(calls) == 1
    changes = [row for row in records(host) if row.get("event") == "career-preload"]
    assert len(changes) == 1 and changes[0]["status"]["state"] == "left"


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
    activity = CollectedActivity(readiness)
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
    host.events.imported(host.events.failures, module.__name__)
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
            assert getattr(error, "_launcher_fatal_logged", False)
            sys.excepthook(*info)
            assert recorder.saved == [error, error] and delegated == [error]
    result = records(host)
    failures = [row for row in result if row.get("fatal")]
    assert len(failures) == 1 and failures[0]["activity"]["kind"] == "in_save"
    assert "Original native crash note" in failures[0]["traceback"] and "FULL END" in failures[0]["reason"]
