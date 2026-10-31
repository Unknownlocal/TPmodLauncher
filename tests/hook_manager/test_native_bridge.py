# SPDX-License-Identifier: MPL-2.0
"""Inject our bridge into an owned Python process, exercising real GIL entry and restoration."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.native
def test_real_bridge_initialises_hooks_and_restores_before_detach(
    native_project, tmp_path
):
    root, client = native_project.root, native_project.client
    runtime = tmp_path / "owned Python target"
    if os.name == "nt":
        shutil.copytree(client.parent, runtime)
    else:
        runtime.mkdir()
        shutil.copy2(native_project.target, runtime / native_project.target.name)
    target = runtime / native_project.target.name
    helper = tmp_path / "owned_helper.py"
    (tmp_path / "owned_native_fixture.py").write_text(
        "def method(value):\n    return value + 1\n"
    )
    shutil.copyfile(FIXTURES / "native_bridge_helper.py", helper)
    menu = tmp_path / "ui/screens"
    menu.mkdir(parents=True)
    (menu.parent / "__init__.py").touch()
    (menu / "__init__.py").touch()
    shutil.copyfile(FIXTURES / "native_menu_fixture.py", menu / "hub_screen.py")
    shutil.copyfile(FIXTURES / "native_settings_fixture.py", menu / "options_screen.py")
    shutil.copyfile(FIXTURES / "native_themes_fixture.py", tmp_path / "native_themes_fixture.py")
    shutil.copyfile(FIXTURES.parents[1].parent / "examples/theme_mod/src/palette.py", tmp_path / "palette.py")
    output = tmp_path / "results"
    output.mkdir()
    probe = root / "launcher/bridge_probe.py"
    shutil.copyfile(FIXTURES / "native_bridge_probe.py", probe)
    shutil.copyfile(
        FIXTURES.parents[1] / "fixtures/native_test_binding.py",
        root / "launcher/native_test_binding.py",
    )
    try:
        result = subprocess.run(
            [
                str(client),
                "-I",
                "-S",
                "-B",
                str(probe),
                str(target),
                str(helper),
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        active = json.loads((output / "active.json").read_text())
        assert active["value"] == 9 and active["status"]["groups"] == {
            "owned": "active"
        }
        closed = json.loads((output / "closed.json").read_text())
        assert closed["value"] == 4 and closed["status"]["state"] == "stopped"
        assert (
            closed["status"]["groups"] == {"owned": "removed"}
            and closed["observer_removed"]
        )
        assert not closed["status"]["errors"]
        assert closed["pack"] == {"state": "stopped"}
        assert closed["menu"] == ["Career", "Exit"] and closed["menu_restored"]
        assert closed["disabled_message_restored"]
        assert closed["settings_tabs"] == [["game", "Game"], ["graphics", "Graphics"]]
        assert closed["settings_restored"]
        assert closed["theme_background"] == [5, 10, 19]
        assert closed["theme_choices"] == ["blue", "emerald"] and closed["theme_restored"]
        menu = json.loads((output / "menu.json").read_text())
        assert menu == {
            "items": ["Career", "Owned UI", "Exit"],
            "message": "Sandbox UI capability",
            "until": 2510,
            "native": "native Exit",
        }
    finally:
        probe.unlink(missing_ok=True)
        # A regression can strand the helper in a native lock. Clean up only
        # this test's unique executable, never a game process or a reused PID.
        pid_file = output / "target.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text(encoding="ascii"))
            if os.name == "nt":
                import ctypes as c
                from ctypes import wintypes as w
                kernel = c.WinDLL("kernel32")
                kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = (w.DWORD, w.BOOL, w.DWORD), w.HANDLE
                kernel.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))
                kernel.TerminateProcess.argtypes, kernel.CloseHandle.argtypes = (w.HANDLE, w.UINT), (w.HANDLE,)
                handle = kernel.OpenProcess(0x1000 | 1, False, pid)
                if handle:
                    try:
                        path, size = c.create_unicode_buffer(32768), w.DWORD(32768)
                        if kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(size)):
                            if Path(path.value).resolve() == target.resolve():
                                kernel.TerminateProcess(handle, 1)
                    finally:
                        kernel.CloseHandle(handle)
            else:
                import signal
                try:
                    descriptor = os.pidfd_open(pid)
                except ProcessLookupError:
                    pass
                else:
                    try:
                        if Path(f"/proc/{pid}/exe").resolve(strict=True) == target.resolve():
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    except (ProcessLookupError, FileNotFoundError):
                        pass  # The pinned helper exited before cleanup.
                    finally:
                        os.close(descriptor)
