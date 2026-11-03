# SPDX-License-Identifier: MPL-2.0
"""Exercise real console input, helper ownership and completed-log presentation."""

import json
import io
import sys
import threading

import pytest

from launcher.process_exit import ProcessExit
from launcher import entry
from launcher.diagnostics.failure_session import FailureSession

from tests.launcher.fixtures.failure_support import launch_notice, wait_for


@pytest.mark.parametrize("key", ["key", "ctrl"])
def test_any_key_dismisses_pending_dialog_after_cleanup(launch_notice, key):
    process, directory = launch_notice("late")
    process.stdin.write((key + "\n").encode())
    process.stdin.flush()
    assert process.wait(timeout=5) == 37
    assert not (directory / "dialog-ready").exists()
    finished = json.loads((directory / "finished.json").read_text())
    assert finished["terminal_restored"] and finished["failure"]["activity"]["kind"] == "saving"
    console = (directory / "console.txt").read_text(encoding="utf-8")
    assert "Mod Launcher could not continue" in console and "while saving" in console
    assert "Original saving failure" in console and "Press any key" in console
    log = (directory / "modLauncher/logs/launcher.log").read_text(encoding="utf-8")
    assert "END OF ORIGINAL CAUSE" in log and "Owned cleanup complete" in log


def test_dialog_dismissal_cancels_console_input(launch_notice):
    process, directory = launch_notice("dialog")
    assert process.wait(timeout=5) == 37
    assert (directory / "dialog-ready").exists() and (directory / "log-requested").exists()
    assert json.loads((directory / "finished.json").read_text())["terminal_restored"]


@pytest.mark.parametrize("mode", ["failure", "missing"])
def test_ui_failures_and_absent_input_do_not_dismiss_or_spin(launch_notice, mode):
    process, directory = launch_notice(mode)
    console_path = directory / "console.txt"
    if mode == "failure":
        wait_for(lambda: "Fixture editor could not open" in console_path.read_text(encoding="utf-8")
                 and "Fixture dialog could not open" in console_path.read_text(encoding="utf-8"))
    assert process.poll() is None
    process.stdin.write(b"dismiss\n")
    process.stdin.flush()
    assert process.wait(timeout=5) == 37
    console = console_path.read_text(encoding="utf-8")
    assert console.count("No interactive console input is available") == 1
    log = (directory / "modLauncher/logs/launcher.log").read_text(encoding="utf-8")
    assert "END OF ORIGINAL CAUSE" in log and "PRESENTATION ERROR" in log
    assert json.loads((directory / "finished.json").read_text())["failure"]["reason"] == "Original saving failure"


def test_dialog_startup_deadline_does_not_expire_console_reading(launch_notice):
    process, directory = launch_notice("timeout")
    wait_for(lambda: "Error dialog startup timed out" in (directory / "console.txt").read_text(encoding="utf-8"))
    assert process.poll() is None and not (directory / "dialog-ready").exists()
    process.stdin.write(b"key\n")
    process.stdin.flush()
    assert process.wait(timeout=5) == 37


def test_console_owner_exit_closes_only_its_helper(launch_notice):
    process, directory = launch_notice("owner")
    wait_for(lambda: (directory / "dialog-ready").exists())
    pid = json.loads((directory / "helper.json").read_text())["pid"]
    exited = threading.Event()
    owner = ProcessExit(pid, sys._base_executable, lambda error: exited.set())
    try:
        owner.start()
        process.kill()
        process.wait(timeout=5)
        assert exited.wait(3), "Presentation helper outlived its console owner"
    finally:
        owner.close()


def test_default_backend_failure_retains_console_until_key(launch_notice):
    process, directory = launch_notice("default")
    wait_for(lambda: "Error dialog unavailable on this host" in (directory / "console.txt").read_text(encoding="utf-8"))
    pid = json.loads((directory / "helper.json").read_text())["pid"]
    exited = threading.Event()
    owner = ProcessExit(pid, sys._base_executable, lambda error: exited.set())
    try:
        owner.start()
        assert process.poll() is None
        process.stdin.write(b"key\n")
        process.stdin.flush()
        assert process.wait(timeout=5) == 37 and exited.wait(3)
    finally:
        owner.close()


def test_duplicate_notice_does_not_append_to_or_claim_the_active_log(tmp_path, monkeypatch, capsys):
    root = tmp_path / "modLauncher"
    log = root / "logs/launcher.log"
    log.parent.mkdir(parents=True)
    log.write_text("Evidence belonging to the active launch", encoding="utf-8")
    monkeypatch.setattr(entry, "__file__", str(root / "launcher/entry.py"))
    def present(outcome, *, owned_log, wait):
        assert not owned_log and not wait
        FailureSession(outcome, owned_log=owned_log).run(wait=wait)
    with entry._diagnostics.log_ownership(root / ".state/controller.lock"):
        assert entry.main(["--check-only"], presentation=present) == 1
    assert log.read_text(encoding="utf-8") == "Evidence belonging to the active launch"
    text = capsys.readouterr().err
    assert "No completed log for this launch:" in text and "Full details:" not in text


def test_closed_console_and_failed_notice_preserve_original_failure_code(tmp_path, monkeypatch):
    root = tmp_path / "modLauncher"
    monkeypatch.setattr(entry, "__file__", str(root / "launcher/entry.py"))
    def fail(root, arguments, command, logger, lock_path, log_path):
        entry._diagnostics.handle_error(logger, RuntimeError("Original completed failure"))
        return 37
    monkeypatch.setattr(entry, "_run", fail)
    console = io.StringIO()
    def unavailable(*args, **kwargs):
        console.close()
        raise OSError("Notice source unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stderr", console)
        code = entry.main(["--", "owned inert command"], presentation=unavailable)
    assert code == 37
    text = (root / "logs/launcher.log").read_text(encoding="utf-8")
    assert "Original completed failure" in text and "LAUNCH FAILURE DETAILS" in text
