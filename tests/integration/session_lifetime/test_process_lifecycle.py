# SPDX-License-Identifier: MPL-2.0
"""Test game-command validation, event ordering, restart gaps and controller cleanup."""

import os
import json
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

FIXTURES = Path(__file__).resolve().parents[2] / "launcher/fixtures"


class Session:
    # This fake models Frida's event contract; tests choose when native events arrive.
    def __init__(self, device, pid):
        self.device = device
        self.pid = pid
        self.handlers = {}
        self.is_detached = False
        self.gating = False

    def on(self, signal, callback):
        self.handlers[signal] = callback

    def enable_child_gating(self):
        self.gating = True
        self.device.calls.append(("gate", self.pid))
        if self.device.failure == "gate":
            raise RuntimeError("gating failed")

    def disable_child_gating(self):
        self.gating = False
        self.device.calls.append(("ungate", self.pid))
        # Deliver a late child during cleanup to exercise the queued-callback path.
        if self.device.late is not None:
            child, self.device.late = self.device.late, None
            self.device.pending[child.pid] = child
            self.device.handlers["child-added"](child)

    def detach(self):
        self.device.calls.append(("detach", self.pid))
        self.is_detached = True
        self.handlers["detached"]("application-requested")


class Device:
    def __init__(self, game):
        self.game = str(game)
        self.processes = {}
        self.handlers = {}
        self.sessions = {}
        self.pending = {}
        self.calls = []
        self.failure = None
        self.late = None

    def on(self, signal, callback):
        self.handlers[signal] = callback

    def off(self, signal, callback):
        assert self.handlers[signal] is callback
        del self.handlers[signal]

    def enumerate_processes(self, **options):
        assert options["scope"] == "metadata"
        pids = options.get("pids", self.processes)
        return [
            SimpleNamespace(pid=pid, parameters={"path": self.processes[pid]})
            for pid in pids
            if pid in self.processes
        ]

    def enumerate_pending_children(self):
        return list(self.pending.values())

    def spawn(self, command, **options):
        self.calls.append(("spawn", list(command), options))
        self.processes[10] = self.game
        return 10

    def attach(self, pid):
        self.calls.append(("attach", pid))
        session = Session(self, pid)
        self.sessions[pid] = session
        return session

    def resume(self, pid):
        self.calls.append(("resume", pid))
        self.pending.pop(pid, None)

    def child(self, pid, path, parent=10):
        self.processes[pid] = str(path)
        child = SimpleNamespace(pid=pid, parent_pid=parent, origin="spawn")
        self.pending[pid] = child
        self.handlers["child-added"](child)
        return child


@pytest.fixture
def rig(tmp_path):
    game = tmp_path / "game with spaces.exe"
    game.write_bytes(b"test fixture")
    device = Device(game)
    messages = []
    now = [0.0]
    controller = lifecycle.Controller(
        device,
        [str(game), "argument with spaces", "--game-option"],
        messages.append,
        restart_grace=5,
        clock=lambda: now[0],
    )
    return device, controller, messages, now


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


def test_validation_error_keeps_severity_in_file_and_console(rig, tmp_path, capsys):
    from hook_manager.game_adapters.regulations.guards import RegulationGuards

    device, controller, _, _ = rig
    notices = []
    channel = lambda *a: {}
    channel.bindings = lambda scope: {}
    channel.validation_ready = True
    channel._unavailable = set()
    guard = RegulationGuards(channel, lambda *a: {}, "personnel.contract", submit_log=notices.append)
    # An owned but unadmitted worker produces the actual native diagnostic.
    thread = threading.Thread(target=lambda: guard._decision(lambda: {}, set()))
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    guard.stop()
    payload = json.loads(json.dumps({"event": "compatibility", **notices[0]}))
    path = tmp_path / "launcher.log"
    with lifecycle.reporter(path) as logger:
        controller.submit_log = lifecycle.bind_reporter(logger, component="controller")
        controller.start()
        try:
            session = controller.sessions[10]
            controller.events.put(("bridge", (10, session, {"type": "send", "payload": payload})))
            controller.poll()
            assert controller.sessions[10] is session and not controller.stopping
        finally:
            controller.close()
    for output in (path.read_text(), capsys.readouterr().err):
        assert " ERROR modLauncher.regulations " in output
        assert "pid=10" in output and "REGULATIONS VALIDATION FAILED" in output
        assert "Unscoped worker or reentrant regulation validation" in output


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
    with lifecycle.reporter(first) as logger:
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
        assert "hidden readiness detail" not in output
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
    device.processes.clear()
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
    # Native state has changed, but the controller has not consumed the old notification yet.
    old.is_detached = True
    device.child(20, controller.game)
    old.handlers["detached"]("process-terminated")
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


def test_controller_lock_rejects_other_process_and_recovers_after_holder_exit(tmp_path):
    lock = tmp_path / "controller.lock"
    probe = tmp_path / "lock_probe.py"
    shutil.copyfile(FIXTURES / "controller_lock_probe.py", probe)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[3])
    command = [sys.executable, str(probe), str(lock)]
    with lifecycle.controller_lock(lock):
        result = subprocess.run(
            command, env=environment, capture_output=True, text=True, timeout=20
        )
        assert result.returncode == 7, result.stderr
    result = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_source_mods_follow_controller_session_identity(rig, source_mod, runner_bundle):
    device, controller, messages, _ = rig
    game = Path(controller.game).parent
    for name in ("first", "second"):
        source_mod(game / "data" / name, f"owned.{name}")
    controller.mods_factory = lambda report, bridge: PackMods(game, report, runner_bundle=runner_bundle)
    def send(pid, session, name):
        pack = dict(state="ready", series="formula", id=name, root=str((game / "data" / name).resolve()))
        controller.events.put(("bridge", (pid, session, {"type": "send", "payload": {"event": "pack", "pack": pack}})))
    def drain(scope):
        deadline = time.monotonic() + 30
        while scope.loading is not None:
            assert time.monotonic() < deadline
            controller.poll()
            threading.Event().wait(0.002)
    controller.start()
    try:
        first = controller.sessions[10]
        send(10, first, "first")
        controller.poll()
        initial = controller.pack_mods[10]
        drain(initial)
        old_mod = initial.mods["owned.first"]
        send(10, first, "second")
        controller.poll()
        assert list(initial.mods) == ["owned.first"]
        first.is_detached = True
        device.child(10, controller.game)
        controller.poll()
        assert initial.closed and old_mod.sandbox.closed
        replacement = controller.sessions[10]
        send(10, first, "first")  # A stale source pack cannot reattach to the new session.
        send(10, replacement, "second")
        controller.poll()
        current_scope = controller.pack_mods[10]
        drain(current_scope)
        assert list(current_scope.mods) == ["owned.second"]
        current_mod = current_scope.mods["owned.second"]
    finally:
        controller.close()
    assert current_scope.closed and current_mod.sandbox.closed
    assert not controller.pack_mods and not controller.cleanup_failed
    assert sum("CODE MOD ACTIVE:" in line for line in messages) == 2


def test_controller_cancels_source_job_on_exit_and_keeps_child_handling_responsive(rig, source_mod, runner_bundle, monkeypatch):
    from mod_runtime import preparation
    device, controller, messages, _ = rig
    game = Path(controller.game).parent
    mod = source_mod(game / "data/owned pack", "owned.pending")
    entered, release = threading.Event(), threading.Event()
    results = []
    original = preparation._prepare_group
    def blocked(*args):
        result = original(*args)
        results.append(result)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(preparation, "_prepare_group", blocked)
    controller.mods_factory = lambda report, bridge: PackMods(game, report, runner_bundle=runner_bundle)
    controller.start()
    scope = None
    try:
        first = controller.sessions[10]
        pack = dict(state="ready", series="formula", id=mod.parent.name, root=str(mod.parent.resolve()))
        controller.events.put(("bridge", (10, first, {"type": "send", "payload": {"event": "pack", "pack": pack}})))
        controller.poll()
        scope = controller.pack_mods[10]
        assert entered.wait(30)
        device.child(20, controller.game)
        controller.poll()
        assert 20 in controller.sessions and ("resume", 20) in device.calls
        first.is_detached = True
        first.handlers["detached"]("process-terminated")
        controller.poll()
        assert scope.closed and 10 not in controller.pack_mods and results[0].snapshots is not None
    finally:
        release.set()
        controller.close()
        if scope is not None:
            scope.executor.shutdown(wait=True)
    assert results[0].snapshots is None and results[0].component is None and results[0].compiled is None
    assert not any("CODE MOD ACTIVE:" in line or "CODE MOD REPORT:" in line for line in messages)
    assert not controller.cleanup_failed
