# SPDX-License-Identifier: MPL-2.0
"""Exercise retained Linux console failures without any desktop presentation."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from launcher import entry
from launcher.diagnostics.failure_session import FailureSession
from launcher.diagnostics.linux_failure_channel import LinuxFailureChannel
from launcher.diagnostics.host_lifetime import HostLifetime
from tests.launcher.fixtures.failure_support import wait_for

SESSION = "linux-notice.0123456789"
NATIVE_LOG = "/home/player/Game's $literal/modLauncher/logs/launcher.log"


def test_owner_loss_dismisses_console_after_complete_log_without_desktop_tools(tmp_path, monkeypatch):
    root = tmp_path / "modLauncher"
    monkeypatch.setattr(entry, "__file__", str(root / "launcher/entry.py"))
    reader, writer = os.pipe()
    lifetime = HostLifetime(descriptor=reader)
    os.close(reader)
    console = io.StringIO()
    def desktop(*args):
        raise AssertionError("Linux must not start a presentation helper")
    def session_factory(value, **options):
        return FailureSession(value, input_stream=io.StringIO(), output=console,
                              child_factory=desktop, **options)
    channel = LinuxFailureChannel(NATIVE_LOG, host_lifetime=lifetime,
                                  session_factory=session_factory)
    def fail(root, arguments, command, logger, lock_path, log_path):
        with entry._diagnostics.activity_scope(logger, "saving"):
            entry._diagnostics.handle_error(logger, RuntimeError("original " + "x" * 7000 + " END"), stage="controller")
        return 37
    monkeypatch.setattr(entry, "_run", fail)
    codes = []
    owner = threading.Thread(target=lambda: codes.append(entry.main(["--", "inert"], presentation=channel)), daemon=True)
    owner.start()
    try:
        wait_for(lambda: "Close this launcher window" in console.getvalue())
        assert owner.is_alive() and codes == []
        with pytest.raises(RuntimeError, match="Another controller"):
            with entry._diagnostics.log_ownership(root / ".state/controller.lock"):
                pass
    finally:
        os.close(writer)
        owner.join(timeout=3)
    assert codes == [37]
    text = console.getvalue()
    assert "while saving" in text and "Stage: controller" in text and NATIVE_LOG in text
    assert "Open the log manually" in text
    log = (root / "logs/launcher.log").read_text()
    assert "x" * 7000 + " END" in log and "LAUNCH FAILURE DETAILS" in log
    assert "Linux must not start" not in log
    assert not (root / ".state" / SESSION).exists()
    with entry._diagnostics.log_ownership(root / ".state/controller.lock"):
        pass


def test_check_only_prints_unowned_host_path_and_full_early_details_without_wait(tmp_path, capsys):
    old_log = tmp_path / "earlier-launch.log"
    old_log.write_text("belongs to another launch")
    value = entry._diagnostics.build_launch_outcome(old_log)
    value.update(log_written=True, failure=dict(reason="early failure", stage="startup",
                 activity=None, details="full chained traceback details"))
    LinuxFailureChannel(NATIVE_LOG)(value, owned_log=False, wait=False)
    text = capsys.readouterr().err
    assert "No completed log for this launch: " + NATIVE_LOG in text
    assert "full chained traceback details" in text and "Press any key" not in text
    assert old_log.read_text() == "belongs to another launch"
    assert not (tmp_path / ".state").exists()


def test_console_waits_for_dismissal_without_a_session_directory(tmp_path):
    console = io.StringIO()
    value = entry._diagnostics.build_launch_outcome(tmp_path / "not-owned.log")
    value["failure"] = dict(reason="primary failure", activity=None)
    retained = []
    def factory(value, **options):
        session = FailureSession(value, input_stream=io.StringIO(), output=console, **options)
        retained.append(session)
        return session
    channel = LinuxFailureChannel(NATIVE_LOG, session_factory=factory)
    finished = threading.Event()
    def present():
        channel(value, owned_log=False, wait=True)
        finished.set()
    worker = threading.Thread(target=present, daemon=True)
    worker.start()
    try:
        wait_for(lambda: "Console input unavailable" in console.getvalue())
        assert "primary failure" in console.getvalue() and not finished.is_set()
    finally:
        if retained:
            retained[0].dismiss()
        worker.join(timeout=3)
    assert finished.is_set() and not (tmp_path / "not-owned.log").exists()
    assert not (tmp_path / ".state").exists()


def test_linux_helper_guard_exits_without_starting_desktop_or_waiting_on_owner():
    helper = Path(__file__).resolve().parents[3] / "launcher/diagnostics/presentation_helper.py"
    process = subprocess.Popen([sys._base_executable, "-I", "-S", "-B", str(helper)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process.stdin.write(json.dumps(dict(host_platform="linux")).encode() + b"\n")
        process.stdin.flush()
        # Keep owner input open: without the guard, the unavailable desktop
        # path waits forever rather than getting a misleading owner-EOF pass.
        assert process.wait(timeout=3) == 0
        assert process.stdout.read() == b"" and process.stderr.read() == b""
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
