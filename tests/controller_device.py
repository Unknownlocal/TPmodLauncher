# SPDX-License-Identifier: MPL-2.0
"""Share the controller test device and its fixed-clock setup."""

from types import SimpleNamespace

import pytest

from launcher import process_lifecycle as lifecycle
from tests.controller_session import ControllerSession
from tests.controller_exit import ControllerExit


class ControllerDevice:
    def __init__(self, game):
        self.game = str(game)
        self.processes = {}
        self.handlers = {}
        self.sessions = {}
        self.exit_waits = {}
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
        session = ControllerSession(self, pid)
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

    def exit(self, pid):
        self.processes.pop(pid, None)
        self.exit_waits[pid].exited = True
        self.exit_waits[pid].completed(None)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Create an owned test executable and controller with a controllable clock."""
    game = tmp_path / "game with spaces.exe"
    game.write_bytes(b"test fixture")
    device = ControllerDevice(game)
    monkeypatch.setattr(lifecycle, "ProcessExit", lambda *args: ControllerExit(device, *args))
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
