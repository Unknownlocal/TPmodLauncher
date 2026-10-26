# SPDX-License-Identifier: MPL-2.0
"""Run the real Frida controller against an owned Python helper process tree."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.native
def test_real_frida_follows_game_children_and_releases_unrelated_helper(
    native_project, tmp_path
):
    root, client = native_project.root, native_project.client
    # A separate executable path distinguishes our simulated game from its controller.
    runtime = tmp_path / "synthetic game with spaces"
    if os.name == "nt":
        shutil.copytree(client.parent, runtime)
    else:
        runtime.mkdir()
        shutil.copy2(native_project.target, runtime / native_project.target.name)
    target = runtime / native_project.target.name
    output = tmp_path / "results"
    output.mkdir()
    helper = tmp_path / "game_helper.py"
    shutil.copyfile(FIXTURES / "native_lifecycle_helper.py", helper)
    probe = root / "launcher/lifecycle_probe.py"
    shutil.copyfile(FIXTURES / "native_lifecycle_probe.py", probe)
    shutil.copyfile(
        FIXTURES.parents[1] / "fixtures/native_test_binding.py",
        root / "launcher/native_test_binding.py",
    )
    arguments = ['space and "quotes"', "", "Unicode \u00e9", "--game-option", "x&y"]
    environment = os.environ.copy()
    environment["TPM_TEST_SENTINEL"] = "inherited from launcher"
    command = [
        str(client),
        "-I",
        "-S",
        "-B",
        str(probe),
        str(target),
        "-B",
        str(helper),
        "parent",
        str(output),
        str(client),
        *arguments,
    ]
    try:
        for _ in range(2):
            # Require each helper to produce fresh results rather than reuse the preceding launch's files.
            for name in ("helper.json", "replacement.json"):
                (output / name).unlink(missing_ok=True)
            # Existing content must be replaced even when using the same log and lock paths.
            log_path = root / "logs/native-test.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("previous-launch-content\n", encoding="utf-8")
            process = subprocess.Popen(
                command,
                env=environment,
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
                pytest.fail("Native controller timed out: " + stdout + stderr)
            assert process.returncode == 0, stdout + stderr
            replacement = json.loads(
                (output / "replacement.json").read_text(encoding="utf-8")
            )
            assert replacement == {
                "argv": arguments,
                "cwd": str(runtime),
                "sentinel": "inherited from launcher",
            }
            assert (output / "helper.json").exists()
            log = log_path.read_text()
            assert "previous-launch-content" not in log
            # Linux gates the two fork transitions as well as attaching to both game images.
            assert log.count("ATTACHED pid=") == (4 if os.name == "posix" else 2)
            assert log.count("HOOK MANAGER READY pid=") == 2
            assert "HELPER pid=" in log and "resumed without attachment" in log
            assert log.count("GAME SESSION ENDED") == 1 and "CLEANUP ERROR" not in log
            assert "CONTROLLER STOPPED; cleanup complete" in log
    finally:
        probe.unlink(missing_ok=True)


@pytest.mark.native
@pytest.mark.skipif(os.name != "nt", reason="Windows console close")
@pytest.mark.parametrize("blocked", [False, True], ids=["normal", "blocked-cleanup"])
def test_closing_console_closes_owned_game_window_and_leaves_other_instance(native_project, tmp_path, blocked):
    import ctypes as c
    from ctypes import wintypes as w

    root, client = native_project.root, native_project.client
    runtime = tmp_path / "owned gui runtime"
    shutil.copytree(client.parent, runtime)
    target = runtime / "pythonw.exe"
    probe = root / "launcher/console_shutdown_probe.py"
    shutil.copyfile(FIXTURES / probe.name, probe)
    shutil.copyfile(FIXTURES.parents[1] / "fixtures/native_test_binding.py", root / "launcher/native_test_binding.py")
    owned, unrelated = tmp_path / "owned", tmp_path / "unrelated"
    kernel, user = c.WinDLL("kernel32"), c.WinDLL("user32")
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
    kernel.WaitForSingleObject.argtypes, kernel.WaitForSingleObject.restype = (w.HANDLE, w.DWORD), w.DWORD
    kernel.TerminateProcess.argtypes = (w.HANDLE, w.UINT)
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    user.PostMessageW.argtypes, user.PostMessageW.restype = (w.HWND, w.UINT, w.WPARAM, w.LPARAM), w.BOOL
    startup = subprocess.STARTUPINFO()
    startup.dwFlags, startup.wShowWindow = subprocess.STARTF_USESHOWWINDOW, subprocess.SW_HIDE
    command = [str(client), "-B", str(probe), "blocked" if blocked else "controller", str(target), "-B",
               str(probe), "window", str(owned)]
    if blocked:
        command.append("ignore-close")
    process = subprocess.Popen(command,
        creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    other, owned_handle = None, None

    def ready(path):
        deadline = time.monotonic() + 10
        while not path.exists() and time.monotonic() < deadline:
            assert process.poll() is None, process.communicate(timeout=2)
            time.sleep(.02)
        assert path.exists(), path
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        record = ready(owned.with_suffix(".json"))
        owned_handle = kernel.OpenProcess(0x100000 | 1, False, record["pid"])
        assert owned_handle
        # Start afterward: the ordinary duplicate-instance guard should still
        # reject pre-existing games. This same-executable window is not owned.
        other = subprocess.Popen([str(target), "-B", str(probe), "window", str(unrelated)])
        ready(unrelated.with_suffix(".json"))
        console = ready(root / "console.json")["hwnd"]
        assert console and user.PostMessageW(console, 0x0010, 0, 0)
        stdout, stderr = process.communicate(timeout=12)
        assert owned.with_suffix(".closed").exists(), stdout + stderr
        assert owned.with_suffix(".exited").exists() == (not blocked), stdout + stderr
        assert kernel.WaitForSingleObject(owned_handle, 2000) == 0
        assert other.poll() is None and not unrelated.with_suffix(".closed").exists()
        log = (root / "logs/console-test.log").read_text(encoding="utf-8")
        assert "CONSOLE CLOSE requested" in log and "CLEANUP ERROR" not in log
        if blocked:
            assert (root / "cleanup-started").exists()
        else:
            assert "CONTROLLER STOPPED; cleanup complete" in log and "GAME CLOSE complete" in log
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if owned_handle:
            if kernel.WaitForSingleObject(owned_handle, 0) == 258:
                kernel.TerminateProcess(owned_handle, 1)  # Only the test-owned GUI helper.
            kernel.CloseHandle(owned_handle)
        if other is not None:
            path = unrelated.with_suffix(".json")
            if path.exists():
                user.PostMessageW(json.loads(path.read_text())["hwnd"], 0x0010, 0, 0)
            try:
                other.wait(timeout=3)
            except subprocess.TimeoutExpired:
                other.kill()
                other.wait(timeout=3)
        probe.unlink(missing_ok=True)
