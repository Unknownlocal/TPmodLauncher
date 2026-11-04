# SPDX-License-Identifier: MPL-2.0
"""Exercise completed Proton failures over the actual loopback handover."""

import io
import json
import os
import queue
import socket
import threading

import pytest

from launcher import entry
from launcher.diagnostics.linux_failure_channel import LinuxFailureChannel
from launcher.diagnostics.host_lifetime import HostLifetime
from tests.launcher.fixtures.failure_support import wait_for


def test_host_dismissal_retains_completed_log_and_lock_until_eof(tmp_path, monkeypatch):
    root = tmp_path / "modLauncher"
    session = "linux-notice.0123456789"
    (root / ".state" / session).mkdir(parents=True)
    native_log = "/home/player/Game's $literal/modLauncher/logs/launcher.log"
    channel = LinuxFailureChannel(root, session, native_log)
    monkeypatch.setattr(entry, "__file__", str(root / "launcher/entry.py"))
    controls = queue.Queue()

    class ControlOutput(io.StringIO):
        def write(self, text):
            if text.startswith("MOD_LAUNCHER_NOTICE "):
                controls.put(text.split())
            return super().write(text)

    monkeypatch.setattr("sys.stdout", ControlOutput())

    def fail(root, arguments, command, logger, lock_path, log_path):
        with entry._diagnostics.activity_scope(logger, "saving"):
            entry._diagnostics.handle_error(logger, RuntimeError("original " + "x" * 7000 + " END"))
        return 37

    monkeypatch.setattr(entry, "_run", fail)
    codes = []
    owner = threading.Thread(target=lambda: codes.append(entry.main(["--", "inert"], presentation=channel)), daemon=True)
    owner.start()
    _, published_session, port, token = controls.get(timeout=5)
    assert published_session == session
    # Another local connection cannot dismiss somebody else's notice.
    with socket.create_connection(("127.0.0.1", int(port)), timeout=2) as rejected:
        rejected.sendall(b"wrong token\n")
        assert rejected.recv(1) == b""
    with socket.create_connection(("127.0.0.1", int(port)), timeout=2) as host:
        host.sendall((token + "\n").encode())
        assert host.recv(32) == b"ready\n"
        with pytest.raises(RuntimeError):
            with entry._diagnostics.log_ownership(root / ".state/controller.lock"):
                pytest.fail("Failure presentation released the log lock")
        outcome = json.loads((root / ".state" / session / "outcome.json").read_text())
        assert outcome["host_log_path"] == native_log and outcome["log_written"]
        assert outcome["failure"]["activity"]["kind"] == "saving"
        log = (root / "logs/launcher.log").read_text()
        assert "x" * 7000 + " END" in log and "LAUNCH FAILURE DETAILS" in log
        assert native_log in (root / ".state" / session / "notice.txt").read_text()
        assert owner.is_alive()
    owner.join(timeout=3)
    assert codes == [37]
    with entry._diagnostics.log_ownership(root / ".state/controller.lock"):
        pass


def test_unavailable_host_is_bounded_and_unowned_log_is_never_offered(tmp_path, capsys):
    session = "linux-notice.0123456789"
    directory = tmp_path / ".state" / session
    directory.mkdir(parents=True)
    channel = LinuxFailureChannel(tmp_path, session, "/native/launcher.log", startup_timeout=.05)
    value = entry._diagnostics.build_launch_outcome(tmp_path / "other-launch.log")
    value.update(log_written=True, failure={"reason": "duplicate ownership", "activity": None})
    with pytest.raises(TimeoutError):
        channel(value, owned_log=False, wait=True)
    assert not (directory / "log-complete").exists()
    assert not json.loads((directory / "outcome.json").read_text())["log_written"]
    assert "No completed log for this launch" in capsys.readouterr().err


@pytest.mark.parametrize("partial_auth", [False, True], ids=["waiting-for-console", "partial-authentication"])
def test_owner_loss_wakes_notice_before_a_terminal_connects(tmp_path, monkeypatch, partial_auth):
    reader, writer = os.pipe()
    stream = os.fdopen(reader, "r")
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", stream)
    monkeypatch.setattr("sys.stdout", output)
    lifetime = HostLifetime()
    session = "linux-notice.0123456789"
    (tmp_path / ".state" / session).mkdir(parents=True)
    channel = LinuxFailureChannel(tmp_path, session, "/native/launcher.log", host_lifetime=lifetime)
    outcome = entry._diagnostics.build_launch_outcome(tmp_path / "launcher.log")
    outcome["failure"] = dict(reason="original error", activity=None)
    finished = threading.Event()
    def present():
        channel(outcome, owned_log=False, wait=True)
        finished.set()
    thread = threading.Thread(target=present, daemon=True)
    peer = None
    try:
        thread.start()
        wait_for(lambda: "MOD_LAUNCHER_NOTICE " in output.getvalue())
        if partial_auth:
            port = output.getvalue().split()[2]
            peer = socket.create_connection(("127.0.0.1", int(port)), timeout=2)
            peer.sendall(b"partial")
        os.close(writer)
        writer = None
        assert finished.wait(1), "Notice retained its startup deadline after owner loss"
        assert lifetime.lost.is_set()
    finally:
        if writer is not None:
            os.close(writer)
        thread.join(timeout=2)
        if peer is not None:
            peer.close()
        stream.close()
