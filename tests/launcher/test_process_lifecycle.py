# SPDX-License-Identifier: MPL-2.0
"""Test game-command validation, event ordering, restart gaps and controller cleanup."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from launcher import process_lifecycle as lifecycle
from launcher.pack_mods import PackMods

FIXTURES = Path(__file__).with_name("fixtures")


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
        if self.failure == "attach":
            raise RuntimeError("attachment failed")
        session = Session(self, pid)
        self.sessions[pid] = session
        return session

    def resume(self, pid):
        self.calls.append(("resume", pid))
        self.pending.pop(pid, None)
        if self.failure == "resume":
            raise RuntimeError("resume failed")

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


def test_command_preserves_arguments_and_rejects_another_executable(tmp_path):
    game = tmp_path / "game with spaces.exe"
    game.write_bytes(b"fixture")
    command = [str(game), "", 'space and "quote"', "--flag", "x&y"]
    assert lifecycle.validate_command(command, game) == command
    other = tmp_path / "other" / game.name
    other.parent.mkdir()
    other.write_bytes(b"same filename")
    for invalid in (
        [],
        [str(other)],
        [game.name],
        ["steam://run/123"],
        [str(game), "bad\0arg"],
    ):
        with pytest.raises(lifecycle.LaunchError):
            lifecycle.validate_command(invalid, game)


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


@pytest.mark.parametrize("failure", ["attach", "gate"])
def test_initial_setup_failure_resumes_game_and_removes_tracking(rig, failure):
    device, controller, _, _ = rig
    device.failure = failure
    try:
        with pytest.raises(RuntimeError):
            controller.start()
    finally:
        controller.close()
    assert ("resume", 10) in device.calls
    assert not device.handlers and not controller.sessions
    assert all(not session.gating for session in device.sessions.values())


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


def test_child_attach_failure_releases_child_before_cleanup(rig):
    device, controller, _, _ = rig
    controller.start()
    device.failure = "attach"
    device.child(20, controller.game)
    try:
        with pytest.raises(RuntimeError, match="attachment failed"):
            controller.poll()
    finally:
        controller.close()
    assert ("resume", 20) in device.calls and not device.pending


def test_unowned_child_notification_is_not_attached_or_resumed(rig):
    device, controller, _, _ = rig
    controller.start()
    device.child(20, controller.game, parent=999)
    controller.poll()
    controller.close()
    assert ("attach", 20) not in device.calls and ("resume", 20) not in device.calls
    assert 20 in device.pending


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


def test_child_pid_reuse_is_resumed_again(rig, tmp_path):
    device, controller, _, _ = rig
    controller.start()
    device.child(20, tmp_path / "helper.exe")
    controller.poll()
    device.processes.pop(20)
    device.child(20, controller.game)
    controller.poll()
    assert device.calls.count(("resume", 20)) == 2
    assert 20 in controller.sessions and not controller.held
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


@pytest.mark.parametrize("event", ["lost", "detached"])
def test_unexpected_tracking_loss_fails_and_cleans_up(rig, event):
    device, controller, _, _ = rig
    controller.start()
    if event == "lost":
        device.handlers["lost"]()
    else:
        device.sessions[10].handlers["detached"]("connection-terminated")
    try:
        with pytest.raises(lifecycle.LaunchError):
            controller.poll()
    finally:
        controller.close()
    assert not device.handlers
    assert all(not session.gating for session in device.sessions.values())


def test_existing_game_is_rejected_before_spawn(rig):
    device, controller, _, _ = rig
    device.processes[99] = controller.game
    with pytest.raises(lifecycle.LaunchError, match="already running"):
        controller.start()
    controller.close()
    assert not device.calls


def test_controller_lock_rejects_other_process_and_recovers_after_holder_exit(tmp_path):
    lock = tmp_path / "controller.lock"
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
    result = subprocess.run(
        command, env=environment, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


def test_failed_resume_is_reported_and_cleanup_still_detaches_sessions(rig, tmp_path):
    device, controller, _, _ = rig
    device.failure = "resume"
    frida = SimpleNamespace(get_local_device=lambda: device)
    log = tmp_path / "launcher.log"
    result = lifecycle.run_launcher(
        frida, controller.command, tmp_path / "controller.lock", log
    )
    assert result == 1
    assert "Could not resume" in log.read_text() and "CLEANUP ERROR" in log.read_text()
    assert "CONTROLLER STOPPED; cleanup incomplete" in log.read_text()
    assert not device.handlers and not device.sessions[10].gating
    assert device.sessions[10].is_detached
    # The failed controller's lock must be available despite failed process resumption.
    with lifecycle.controller_lock(tmp_path / "controller.lock"):
        pass


def test_duplicate_launcher_preserves_existing_log(rig, tmp_path):
    device, controller, _, _ = rig
    frida = SimpleNamespace(get_local_device=lambda: device)
    log = tmp_path / "launcher.log"
    log.write_bytes(b"active-session-log\n")
    lock = tmp_path / "controller.lock"
    with lifecycle.controller_lock(lock):
        with pytest.raises(lifecycle.LaunchError, match="Another controller"):
            lifecycle.run_launcher(frida, controller.command, lock, log)
    assert log.read_bytes() == b"active-session-log\n"
    assert not device.calls


@pytest.mark.parametrize("failure", [None, "start", "close"])
def test_bridge_ordering_and_failure_cleanup(rig, failure):
    device, controller, messages, _ = rig
    callbacks = {}

    def pack_message(name):
        pack = {"id": name, "state": "ready"}
        return {"type": "send", "payload": {"event": "pack", "pack": pack}}

    class OwnedBridge:
        def __init__(self, session, pid, message):
            callbacks[pid] = message
            self.pid = pid

        def start(self):
            device.calls.append(("bridge-start", self.pid))
            if failure == "start":
                raise RuntimeError("bridge startup failed")

        def close(self):
            device.calls.append(("bridge-close", self.pid))
            if failure == "close":
                raise RuntimeError("bridge cleanup failed")

    controller.bridge_factory = OwnedBridge
    try:
        if failure == "start":
            with pytest.raises(RuntimeError, match="startup"):
                controller.start()
        else:
            controller.start()
            callbacks[10](
                {
                    "type": "send",
                    "payload": {"event": "manager", "status": {"state": "ready"}},
                }
            )
            controller.poll()
            assert any("HOOK MANAGER READY pid=10" in message for message in messages)
            callbacks[10](pack_message("first"))
            controller.poll()
            assert sum("PACK DISCOVERY pid=10" in message for message in messages) == 1
            old = callbacks[10]
            first = controller.sessions[10]
            first.is_detached = True
            controller._attach(10)
            old(
                {"type": "send", "payload": {"event": "error", "detail": "stale error"}}
            )
            old(pack_message("stale"))
            callbacks[10](pack_message("replacement"))
            controller.poll()  # A detached bridge must not fail the replacement's session.
            discovery = [line for line in messages if "PACK DISCOVERY" in line]
            assert len(discovery) == 2 and '"replacement"' in discovery[-1]
            assert not any('"stale"' in line for line in discovery)
    finally:
        controller.close()
    events = [call[0] for call in device.calls]
    assert events.index("bridge-start") < events.index("resume")
    assert events.index("bridge-close") < events.index("detach")
    assert not controller.sessions and not controller.bridges and not device.pending
    assert controller.cleanup_failed == (failure == "close")


def test_bridge_bootstrap_error_is_not_reported_as_ready(rig):
    _, controller, messages, _ = rig
    controller.start()
    session = controller.sessions[10]
    controller.events.put(
        (
            "bridge",
            (
                10,
                session,
                {
                    "type": "send",
                    "payload": {"event": "error", "detail": "unsupported target"},
                },
            ),
        )
    )
    try:
        with pytest.raises(lifecycle.LaunchError, match="unsupported target"):
            controller.poll()
        assert not any("HOOK MANAGER READY" in message for message in messages)
    finally:
        controller.close()


def test_fork_gates_child_without_bootstrapping_inherited_manager(rig):
    device, controller, _, _ = rig
    started = []
    options = []

    class Bridge:
        def start(self):
            started.append("start")

        def close(self):
            started.append("close")

    def create(session, pid, message, **settings):
        options.append(settings)
        return Bridge()

    controller.bridge_factory = create
    controller.start()
    try:
        child = device.child(20, controller.game)
        child.origin = "fork"
        controller.poll()
        fork_session = controller.sessions[20]
        assert fork_session.gating and 20 in controller.resumed
        assert options == [{}, {"bootstrap": False}]
        child.origin = "exec"
        controller.events.put(("child", child))
        controller.poll()
        assert fork_session.is_detached
        assert controller.sessions[20] is not fork_session
        assert 20 in controller.bridges and options == [{}, {"bootstrap": False}, {}]
    finally:
        controller.close()
    assert started == ["start", "start", "start", "close", "close"]


@pytest.mark.parametrize(
    "transition", ["detached", "missing", "exec", "pid-reuse", "shutdown"]
)
def test_pack_mods_follow_session_identity_and_close_on_every_removal(
    rig, code_mod, transition
):
    device, controller, messages, now = rig
    game_root = Path(controller.game).parent
    for name in ("first", "second"):
        code_mod(game_root / "data" / name, f"owned.{name}")
    controller.mods_factory = lambda report, bridge: PackMods(game_root, report)

    def send_pack(pid, session, name):
        pack = {
            "state": "ready",
            "series": "formula",
            "id": name,
            "root": str((game_root / "data" / name).resolve()),
        }
        message = {"type": "send", "payload": {"event": "pack", "pack": pack}}
        controller.events.put(("bridge", (pid, session, message)))

    controller.start()
    try:
        first = controller.sessions[10]
        send_pack(10, first, "first")
        controller.poll()
        initial = controller.pack_mods[10]
        old_mod = initial.mods["owned.first"]
        send_pack(10, first, "second")
        controller.poll()
        assert list(initial.mods) == ["owned.first"]  # Never reassign a live snapshot.
        if transition != "shutdown":
            if transition in ("detached", "missing"):
                del device.processes[10]
                if transition == "detached":
                    first.handlers["detached"]("process-replaced")
                now[0] = 1.0
                controller.poll()
                replacement_pid = 20
            else:
                replacement_pid = 10
                if transition == "pid-reuse":
                    first.is_detached = True
            child = device.child(replacement_pid, controller.game)
            if transition == "exec":
                child.origin = "exec"
            controller.poll()
            assert initial.closed and old_mod.sandbox.closed
            replacement = controller.sessions[replacement_pid]
            # A delayed old record cannot recreate old mods.
            send_pack(10, first, "first")
            send_pack(replacement_pid, replacement, "second")
            controller.poll()
            active = controller.pack_mods[replacement_pid]
            assert list(active.mods) == ["owned.second"]
            current_mod = active.mods["owned.second"]
        else:
            active, current_mod = initial, old_mod
    finally:
        controller.close()
    assert active.closed and current_mod.sandbox.closed
    assert not controller.pack_mods and not controller.cleanup_failed
    assert sum("CODE MOD ACTIVE:" in line for line in messages) == (
        1 if transition == "shutdown" else 2
    )
