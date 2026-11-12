# SPDX-License-Identifier: MPL-2.0
"""Exercise damaged startup sources before any launch can own the log."""

from pathlib import Path
import shutil
import subprocess
import sys
import io
import os
import threading
import json
from types import SimpleNamespace

import pytest

from tests.launcher.fixtures.failure_support import wait_for
from launcher import entry
from launcher.diagnostics.host_lifetime import HostLifetime


@pytest.mark.parametrize("damage,wait", [("missing-dependencies", True), ("invalid-diagnostics", False)])
def test_damaged_startup_helpers_keep_complete_notice_and_leave_previous_log_alone(tmp_path, damage, wait):
    source = Path(__file__).resolve().parents[2] / "launcher"
    root = tmp_path / "game/modLauncher"
    launcher = root / "launcher"
    launcher.mkdir(parents=True)
    shutil.copyfile(source / "entry.py", launcher / "entry.py")
    if damage == "invalid-diagnostics":
        shutil.copyfile(source / "bundled_dependencies.py", launcher / "bundled_dependencies.py")
        (launcher / "diagnostics").mkdir()
        (launcher / "diagnostics/core.py").write_text("def incomplete(:\n")
    log = root / "logs/launcher.log"
    log.parent.mkdir()
    previous = b"Evidence belonging to a previous launch\n"
    log.write_bytes(previous)
    console = root / "console.txt"
    with console.open("wb") as output:
        process = subprocess.Popen([sys._base_executable, "-I", "-S", "-B", str(launcher / "entry.py"),
                                    *(["--", "never-start-this-game"] if wait else ["--check-only"])],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=output, cwd=tmp_path,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            if wait:
                wait_for(lambda: "Press any key" in console.read_text())
                assert process.poll() is None
                process.stdin.write(b"x")
                process.stdin.flush()
            assert process.wait(timeout=5) == 1
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
    text = console.read_text()
    assert "while starting the launcher" in text and "Stage: startup_helpers" in text
    assert "Traceback (most recent call last)" in text
    assert ("FileNotFoundError" if wait else "SyntaxError") in text
    assert "No completed log for this launch:" in text and "Full details:" not in text
    assert log.read_bytes() == previous and not (root / ".state").exists()


def test_early_notice_retains_full_chain_without_input_until_proton_owner_loss(tmp_path, monkeypatch):
    reader, owner = os.pipe()
    lifetime = HostLifetime(descriptor=reader)
    os.close(reader)
    if os.name == "nt":
        import ctypes
        # This case exercises absent input in a headless host, regardless of
        # whether the developer's pytest process itself owns a console window.
        kernel = SimpleNamespace(GetConsoleWindow=lambda: None)
        monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **options: kernel)
    console = io.StringIO()
    native_path = "/home/player/game/modLauncher/logs/launcher.log"
    presentation = lambda *args, **options: pytest.fail("No diagnostic helpers are available")
    presentation.host_log_path = native_path
    monkeypatch.setattr(sys, "stderr", console)
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    monkeypatch.setattr(entry, "__file__", str(tmp_path / "launcher/entry.py"))
    monkeypatch.setattr(entry, "_startup_failure", dict(reason="Owned bootstrap failure",
                        details="Full chained cause " + "x" * 7000 + " CHAIN END\n"))
    codes, outcome = [], {}
    thread = threading.Thread(target=lambda: codes.append(entry.main(["--", "inert"], outcome=outcome,
                              presentation=presentation, host_lifetime=lifetime)), daemon=True)
    thread.start()
    try:
        wait_for(lambda: "CHAIN END" in console.getvalue())
        assert thread.is_alive() and not codes
    finally:
        os.close(owner)
        thread.join(timeout=3)
    assert codes == [1] and native_path in console.getvalue()
    assert outcome["failure"]["level"] == "ERROR" and outcome["failure"]["stage"] == "startup_helpers"
    assert outcome["log_written"] is False and not (tmp_path / "logs").exists()


@pytest.mark.skipif(os.name != "nt", reason="Owned native Windows console")
def test_redirected_standard_streams_still_show_startup_failure_and_accept_a_native_key(tmp_path):
    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "modLauncher"
    (root / "launcher").mkdir(parents=True)
    shutil.copyfile(source / "launcher/entry.py", root / "launcher/entry.py")
    startup = subprocess.STARTUPINFO()
    startup.dwFlags, startup.wShowWindow = subprocess.STARTF_USESHOWWINDOW, 0
    with (root / "redirected-stderr.txt").open("wb") as output:
        process = subprocess.Popen([sys._base_executable, "-I", "-S", "-B",
                                    str(source / "tests/launcher/fixtures/startup_console_probe.py"), str(root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=output,
                                   creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        try:
            wait_for(lambda: (root / "console.json").exists() or process.poll() is not None)
            assert process.poll() is None, (root / "redirected-stderr.txt").read_text()
            text = json.loads((root / "console.json").read_text())["text"]
            assert "FileNotFoundError" in text and "bundled_dependencies.py" in text
            assert "while starting the launcher" in text and "No completed log for this launch" in text
            process.stdin.write(b"key\n")
            process.stdin.flush()
            assert process.wait(timeout=5) == 1
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()

