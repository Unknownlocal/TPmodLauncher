# SPDX-License-Identifier: MPL-2.0
"""Check launcher tracking, command arguments, file locks and logging."""

import os
import json
from contextlib import nullcontext
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from launcher import process_lifecycle as lifecycle
from launcher.pack_mods import PackMods
from tests.controller_device import rig

FIXTURES = Path(__file__).with_name("fixtures")


def test_exit_during_child_gating_skips_bridge_creation(rig, monkeypatch):
    device, controller, _, _ = rig
    attach = device.attach
    def attach_then_exit(pid):
        session = attach(pid)
        session.enable_child_gating = lambda: device.exit(pid)
        return session
    monkeypatch.setattr(device, "attach", attach_then_exit)
    def forbidden(*args, **kwargs):
        raise AssertionError("Constructed a bridge for a retired process")
    controller.bridge_factory = forbidden
    try:
        controller.start()
        controller.poll()
        assert not controller.sessions and not controller.bridges and not controller.cleanup_failed
    finally:
        controller.close()


def test_queued_script_failure_keeps_full_stack_after_owner_cancellation(rig):
    device, controller, _, _ = rig
    controller.start()
    session = controller.sessions[10]
    stack = "Owned script stack " + "x" * 5000 + " FULL STACK END"
    controller._queue_bridge(10, session, {"type": "error", "description": "Owned script failure END", "stack": stack})
    device.exit(10)
    try:
        with pytest.raises(lifecycle.LaunchError, match="Owned script failure END") as failure:
            controller.poll()
        assert stack in "\n".join(failure.value.__notes__)
    finally:
        controller.close()


def test_exit_during_live_bridge_shutdown_completes_cleanup(rig):
    from launcher.game_bridge import Bridge

    device, controller, _, _ = rig
    controller.start()
    session = controller.sessions[10]
    session.create_script = lambda *args, **kwargs: SimpleNamespace(on=lambda *args: None, is_destroyed=False, unload=lambda: None)
    bridge = Bridge(None, session, "", lambda *args: None)
    bridge.loaded = True
    bridge.cancellation = controller.cancellations[10]
    controller.bridges[10] = bridge
    bridge._post = lambda *args: device.exit(10)
    controller.close()
    assert not controller.sessions and not bridge.waiters and not controller.cleanup_failed
    assert device.exit_waits[10].closed


def test_retirement_signal_handles_native_disconnect_before_detached_flag(rig):
    device, controller, messages, _ = rig
    controller.start()
    def disconnect():
        # The callback's cancellation flag can precede Frida's detached property.
        controller.cancellations[10].cancel()
        raise RuntimeError("Owned transport already closed")
    controller.sessions[10].disable_child_gating = disconnect
    controller.close()
    assert not controller.cleanup_failed and not controller.sessions
    assert not any("CLEANUP ERROR" in message for message in messages)


@pytest.mark.parametrize("during_cleanup", [False, True])
def test_script_failure_delivered_during_final_reporting_survives_retirement(rig, during_cleanup):
    from launcher.game_bridge import Bridge

    device, controller, messages, _ = rig
    controller.start()
    session = controller.sessions[10]
    session.create_script = lambda *args, **kwargs: SimpleNamespace(on=lambda *args: None)
    bridge = Bridge(None, session, "", lambda *args: None)
    controller.bridges[10] = bridge
    stack = "Late stack " + "x" * 5000 + " FULL LATE STACK END"
    bridge.finish_reporting = lambda: controller._queue_bridge(10, session, {
        "type": "error", "description": "Late owned script failure END", "stack": stack})
    device.exit(10)
    try:
        if during_cleanup:
            controller.close()
            assert controller.cleanup_failed
        else:
            with pytest.raises(lifecycle.LaunchError, match="Late owned script failure END"):
                controller.poll()
        assert any(stack in message for message in messages)
    finally:
        controller.close()


def test_career_compatibility_notice_reports_failure_and_keeps_game_running(rig):
    device, controller, messages, _ = rig
    controller.start()
    try:
        session = controller.sessions[10]
        detail = "REGULATIONS COMPATIBILITY FAILED career.preload; continuing native game"
        controller.events.put(("bridge", (10, session, {
            "type": "send", "payload": {"event": "compatibility", "detail": detail}})))
        controller.poll()
        assert any(detail in message for message in messages)
        assert controller.sessions[10] is session and not session.is_detached
        assert not controller.stopping
    finally:
        controller.close()


def test_pack_logging_retains_severity_and_repeated_sessions_release_handlers(rig, tmp_path, capsys):
    import logging

    device, controller, _, _ = rig
    game = Path(controller.game).parent
    pack = game / "data/owned pack"
    bad = pack / "broken"
    bad.mkdir(parents=True)
    (bad / "mod.toml").write_text("invalid TOML")
    logger = logging.getLogger("modLauncher")
    before = list(logger.handlers), logger.level, logger.propagate
    first = tmp_path / "first.log"
    with lifecycle.reporter(first, log_level="DEBUG") as logger:
        controller.submit_log = lifecycle.bind_reporter(logger, component="controller")
        controller.mods_factory = lambda report, bridge: PackMods(game, report)
        controller.start()
        session = controller.sessions[10]
        def send(payload):
            controller.events.put(("bridge", (10, session, {"type": "send", "payload": payload})))
            controller.poll()
        try:
            send({"event": "pack", "pack": dict(state="ready", series="formula", id=pack.name, root=str(pack.resolve()))})
            assert controller.pack_mods[10].failed == 1
            # Malformed severity and guest control characters stay ordinary
            # bounded diagnostics; neither changes the session's lifetime.
            send({"event": "diagnostic", "level": [], "detail": "unsafe\n\x1bmessage" + "x" * 5000})
            send({"event": "waiting", "level": "DEBUG", "detail": "hidden readiness detail"})
            assert controller.sessions[10] is session and not controller.stopping
        finally:
            controller.close()
    first_text = first.read_text()
    console = capsys.readouterr().err
    for output in (first_text, console):
        assert "WARNING modLauncher.mods pid=10 CODE MOD REJECTED" in output
        invalid = next(line for line in output.splitlines() if "Invalid diagnostic severity" in line)
        assert "WARNING" in invalid and "\x1b" not in invalid and len(invalid) < 4400
    assert "DEBUG modLauncher.bridge pid=10 hidden readiness detail" in first_text
    assert console == first_text
    assert (list(logger.handlers), logger.level, logger.propagate) == before
    second = tmp_path / "second.log"
    with lifecycle.reporter(second) as logger:
        lifecycle.write_log(logger, "second session")
    assert second.read_text().count("second session") == 1 and first.read_text() == first_text
    assert (list(logger.handlers), logger.level, logger.propagate) == before


def test_large_pack_error_keeps_cause_when_owner_list_is_truncated(tmp_path, capsys):
    path = tmp_path / "launcher.log"
    with lifecycle.reporter(path) as logger:
        scope = PackMods(tmp_path, logger)
        scope._log("SOURCE GROUP FAILED", level="ERROR",
                   owners=["a" * 126 + f"{i:02}" for i in range(32)], detail="Owned root cause")
        scope.close()
    for output in (path.read_text(), capsys.readouterr().err):
        assert output.count("SOURCE GROUP FAILED") == 1
        assert "ERROR modLauncher.mods" in output and "Owned root cause" in output
        assert "[truncated]" in output and len(output) < 4400


def test_fatal_preload_status_keeps_complete_remote_error_and_closes_controller(rig, tmp_path, capsys, monkeypatch):
    import json
    from hook_manager.manager import Manager
    from launcher.regulations.runtime import RegulationRuntime

    device, _, _, _ = rig
    manager = Manager()
    secret_local = "LOCAL VALUES MUST NOT APPEAR"
    cause = "Original native cause " + "x" * 5000 + " END OF CAUSE"

    def native_load(depth):
        if depth:
            return native_load(depth - 1)
        raise ValueError(cause)

    try:
        try:
            native_load(6)
        except ValueError as error:
            raise RuntimeError("Save loading failed") from error
    except RuntimeError as error:
        error.add_note("Native loading note")
        manager.store_error(error)
    # This single status must be enough even if no later diagnostic is delivered.
    status = json.loads(json.dumps({"state": "unavailable", **manager.error_record}))
    runtime = RegulationRuntime(SimpleNamespace(owner_ids=()), {})
    runtime._bridge = object()
    scope = PackMods(tmp_path, lambda message: None)
    scope.regulations = runtime

    class Shutdown:
        def __init__(self, *args):
            self.requested = threading.Event()
        def __enter__(self):
            return self
        def track(self, pid):
            pass
        def request(self):
            self.requested.set()
        def __exit__(self, *args):
            assert self.requested.is_set() and scope.closed

    class Bridge:
        def __init__(self, session, pid, on_message):
            self.on_message = on_message
        def start(self):
            self.on_message({"type": "send", "payload": {"event": "career-preload", "status": status}})
        def close(self):
            raise RuntimeError("Owned bridge cleanup failure")

    # Assign the real pack scope when the owned session is attached.
    original = lifecycle.Controller._attach
    def attach(controller, pid, **options):
        result = original(controller, pid, **options)
        controller.pack_mods[pid] = scope
        return result
    monkeypatch.setattr(lifecycle.Controller, "_attach", attach)
    monkeypatch.setattr(lifecycle, "ConsoleShutdown", Shutdown)
    path = tmp_path / "launcher.log"
    assert lifecycle.run_launcher(SimpleNamespace(get_local_device=lambda: device), [device.game],
                                  tmp_path / "lock", path, bridge_factory=Bridge) == 1
    assert not device.handlers and all(session.is_detached for session in device.sessions.values())
    for output in (path.read_text(encoding="utf-8"), capsys.readouterr().err):
        assert "Native regulation loading protection became unavailable" in output
        assert cause in output and "ValueError:" in output and "in native_load" in output
        assert "direct cause" in output and "Native loading note" in output
        assert "CONTROLLER STOPPED; cleanup incomplete" in output
        assert secret_local not in output
        assert "Owned bridge cleanup failure" in output
    saved = json.loads(path.read_text(encoding="utf-8").rpartition("LAUNCH FAILURE DETAILS ")[2])
    assert saved["failure"]["stage"] == "controller" and saved["cleanup"] == "failed"
    assert saved["failure"]["reason"] == "Native regulation loading protection became unavailable"


def test_cleanup_failure_becomes_a_fatal_outcome_without_an_earlier_error(rig, tmp_path, monkeypatch):
    device, _, _, _ = rig
    requested = threading.Event()
    requested.set()  # A console-close request before startup skips spawning the game.
    shutdown = SimpleNamespace(requested=requested, request=requested.set)
    monkeypatch.setattr(lifecycle, "ConsoleShutdown", lambda *args: nullcontext(shutdown))
    def unavailable():
        raise RuntimeError("Owned pending children could not be released")
    monkeypatch.setattr(device, "enumerate_pending_children", unavailable)
    path = tmp_path / "launcher.log"
    assert lifecycle.run_launcher(SimpleNamespace(get_local_device=lambda: device), [device.game],
                                  tmp_path / "lock", path) == 1
    text = path.read_text(encoding="utf-8")
    assert "Owned pending children could not be released" in text and "cleanup incomplete" in text
    saved = json.loads(text.rpartition("LAUNCH FAILURE DETAILS ")[2])
    assert saved["failure"]["stage"] == "cleanup" and saved["cleanup"] == "failed"
    assert not device.handlers



def test_game_is_gated_before_resume_with_original_environment_and_cwd(rig):
    device, controller, _, _ = rig
    try:
        controller.start()
        assert [call[0] for call in device.calls] == [
            "spawn",
            "attach",
            "gate",
            "resume",
        ]
        _, arguments, options = device.calls[0]
        assert arguments == controller.command
        assert options["cwd"] == str(Path(controller.command[0]).parent)
        assert options["envp"] == dict(os.environ)
        assert options["stdio"] == "inherit"
    finally:
        controller.close()
    assert not device.handlers and not device.sessions[10].gating


def test_initial_setup_failure_resumes_game_and_removes_tracking(rig):
    device, controller, _, _ = rig
    device.failure = "gate"
    try:
        with pytest.raises(RuntimeError):
            controller.start()
    finally:
        controller.close()
    assert ("resume", 10) in device.calls
    assert not device.handlers and not controller.sessions
    assert all(not session.gating for session in device.sessions.values())


def test_console_close_during_setup_does_not_start_game(rig, monkeypatch):
    device, controller, _, _ = rig
    controller.shutdown = SimpleNamespace(requested=threading.Event())
    enumerate_processes = device.enumerate_processes

    def closing(**options):
        controller.shutdown.requested.set()
        return enumerate_processes(**options)

    monkeypatch.setattr(device, "enumerate_processes", closing)
    try:
        controller.start()
        assert not device.processes and not controller.sessions and not controller.held
    finally:
        controller.close()
    assert not device.handlers and not controller.cleanup_failed


def test_children_are_verified_and_helpers_and_late_children_are_released(
    rig, tmp_path
):
    device, controller, _, _ = rig
    controller.start()
    game_child = device.child(20, controller.game)
    device.child(21, tmp_path / "helper.exe")
    controller.poll()
    assert ("attach", 20) in device.calls and ("attach", 21) not in device.calls
    assert not device.pending
    # This appears while the first session is being detached.
    device.processes[22] = str(tmp_path / "helper.exe")
    device.late = SimpleNamespace(pid=22, parent_pid=20, origin="spawn")
    controller.close()
    assert ("resume", 22) in device.calls and ("attach", 22) not in device.calls
    assert not device.pending and not device.handlers and not controller.held
    assert not controller.cleanup_failed


def test_restart_gap_and_stale_detached_event_do_not_end_session(rig):
    device, controller, _, now = rig
    controller.start()
    first = controller.sessions[10]
    del device.processes[10]
    first.handlers["detached"]("process-terminated")
    assert not controller.poll()
    now[0] = 4.0
    assert not controller.poll()
    device.child(20, controller.game)
    assert not controller.poll()
    current = controller.sessions[20]
    # A delayed event with the same PID but an old session must not remove the replacement.
    controller.events.put(("detached", (20, first, "process-terminated")))
    assert not controller.poll() and controller.sessions[20] is current
    for pid in list(device.processes):
        if pid in controller.sessions:
            device.exit(pid)
        else:
            device.processes.pop(pid)
    now[0] = 5.0
    assert not controller.poll()
    now[0] = 9.9
    assert not controller.poll()
    now[0] = 10.0
    assert controller.poll()
    controller.close()


def test_reused_game_pid_attaches_again_before_delayed_old_detach(rig):
    device, controller, _, now = rig
    controller.start()
    device.child(20, controller.game)
    controller.poll()
    old = controller.sessions[20]
    old_waiter = device.exit_waits[20]
    # Native state has changed, but the controller has not consumed the old notification yet.
    old.is_detached = True
    device.child(20, controller.game)
    old.handlers["detached"]("process-terminated")
    old_waiter.completed(None)
    try:
        assert not controller.poll()
        replacement = controller.sessions[20]
        assert replacement is not old and not replacement.is_detached
        assert device.calls.count(("attach", 20)) == 2
        assert device.calls.count(("resume", 20)) == 2
        assert not device.pending and not controller.held
        # The old parent then exits; the replacement must keep the controller alive past the grace.
        device.processes.pop(10)
        device.sessions[10].handlers["detached"]("process-terminated")
        controller.poll()
        now[0] = 10.0
        assert not controller.poll() and controller.sessions[20] is replacement
    finally:
        controller.close()


def test_idle_controller_blocks_until_owned_exit_and_restart_deadline(rig, monkeypatch):
    device, controller, _, _ = rig
    controller.clock = time.monotonic
    controller.restart_grace = .03
    controller.start()
    entered, released = threading.Event(), threading.Event()
    def unexpected(*args, **kwargs):
        raise AssertionError("Idle controller inspected processes or polled mods")
    monkeypatch.setattr(device, "enumerate_processes", unexpected)
    controller.pack_mods[10] = SimpleNamespace(poll=unexpected, close=released.set)
    get, waits = controller.events.get, []
    def receive(*args, **kwargs):
        waits.append(kwargs.get("timeout"))
        entered.set()
        return get(*args, **kwargs)
    monkeypatch.setattr(controller.events, "get", receive)
    def exit_without_frida_notification():
        assert entered.wait(2)
        device.exit(10)
    worker = threading.Thread(target=exit_without_frida_notification)
    try:
        assert not controller.poll()
        entered.clear()
        worker.start()
        assert not controller.poll(timeout=None)
        assert released.is_set() and not controller.sessions
        assert controller.poll(timeout=None)
        assert waits[-1] is None
    finally:
        worker.join(2)
        controller.close()


def test_early_deadline_wake_does_not_leave_session_end_blocked(rig, monkeypatch):
    device, controller, _, now = rig
    timer_type, scheduled = threading.Timer, []
    finished, errors = threading.Event(), []

    def timer(delay, callback):
        number = len(scheduled)
        def wake():
            # First wake is early. Only a newly armed timer advances the test
            # clock to its deadline; no manual event may finish the session.
            if number:
                now[0] = controller.restart_grace
            callback()
        scheduled.append(delay)
        return timer_type(0, wake)

    monkeypatch.setattr(lifecycle.threading, "Timer", timer)
    controller.start()
    device.exit(10)

    def finish_session():
        try:
            while not controller.poll(timeout=None):
                pass
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    worker = threading.Thread(target=finish_session)
    try:
        worker.start()
        assert finished.wait(2), "Session end lost its only deadline wake"
        assert not errors and not controller.sessions and not controller.held
    finally:
        # Release a regressed implementation's blocked queue wait before cleanup.
        now[0] = controller.restart_grace
        controller.events.put(("shutdown", None))
        worker.join(2)
        controller.close()


def test_unexpected_tracking_loss_fails_and_cleans_up(rig):
    device, controller, _, _ = rig
    controller.start()
    device.handlers["lost"]()
    try:
        with pytest.raises(lifecycle.LaunchError):
            controller.poll()
    finally:
        controller.close()
    assert not device.handlers
    assert all(not session.gating for session in device.sessions.values())


def test_cleanup_accepts_confirmed_exit_before_frida_detached_notification(rig):
    device, controller, messages, _ = rig
    controller.start()
    session = controller.sessions[10]
    def exit_during_gating_cleanup():
        device.exit(10)
        raise RuntimeError("The process transport has closed")
    session.disable_child_gating = exit_during_gating_cleanup
    controller.close()
    assert not controller.cleanup_failed and not device.handlers and not controller.sessions
    assert not any("CLEANUP ERROR" in message for message in messages)


def test_controller_lock_rejects_other_process_and_recovers_after_holder_exit(tmp_path, monkeypatch):
    from launcher import entry

    lock = tmp_path / ".state/controller.lock"
    log = tmp_path / "logs/launcher.log"
    log.parent.mkdir()
    log.write_text("Active controller evidence", encoding="utf-8")
    before = log.read_bytes(), log.stat().st_mtime_ns
    monkeypatch.setattr(entry, "__file__", str(tmp_path / "launcher/entry.py"))
    probe = tmp_path / "lock_probe.py"
    shutil.copyfile(FIXTURES / "controller_lock_probe.py", probe)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    command = [sys.executable, str(probe), str(lock)]
    with lifecycle.controller_lock(lock):
        result = subprocess.run(
            command, env=environment, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 7, result.stderr
        outcome = {}
        assert entry.main(["--check-only"], outcome=outcome) == 1
        assert (log.read_bytes(), log.stat().st_mtime_ns) == before
        assert outcome["failure"]["stage"] == "log_ownership" and not outcome["log_written"]
    result = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
