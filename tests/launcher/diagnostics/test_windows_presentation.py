# SPDX-License-Identifier: MPL-2.0
"""Exercise native windows and shell dispatch using only owned applications."""

from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import json
import os
import sys
import threading

import pytest

from launcher.process_exit import ProcessExit
from tests.launcher.fixtures.failure_support import launch_notice, wait_for

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows desktop fixtures")


def marker(directory, name):
    path = directory / (name + ".json")
    wait_for(path.exists)
    return json.loads(path.read_text(encoding="utf-8"))


def user_api():
    user = c.WinDLL("user32", use_last_error=True)
    for name, args, result in (
        ("GetWindowThreadProcessId", (w.HWND, c.POINTER(w.DWORD)), w.DWORD),
        ("IsWindowVisible", (w.HWND,), w.BOOL),
        ("GetDlgItem", (w.HWND, c.c_int), w.HWND),
        ("SendMessageW", (w.HWND, w.UINT, w.WPARAM, w.LPARAM), c.c_ssize_t),
        ("PostMessageW", (w.HWND, w.UINT, w.WPARAM, w.LPARAM), w.BOOL),
    ):
        function = getattr(user, name)
        function.argtypes, function.restype = args, result
    return user


def visible(info):
    user, pid = user_api(), w.DWORD()
    assert user.GetWindowThreadProcessId(info["window"], c.byref(pid))
    assert pid.value == info["pid"]  # Never address windows by a shared title.
    assert user.IsWindowVisible(info["window"])
    return user


@contextmanager
def exit_wait(info):
    done = threading.Event()
    owner = ProcessExit(info["pid"], sys._base_executable, lambda error: done.set())
    try:
        owner.start()
        yield done
    finally:
        owner.close()


@contextmanager
def editor_window(directory):
    info = marker(directory, "editor")
    visible(info)
    with exit_wait(info) as done:
        try:
            yield info, done
        finally:
            (directory / "close-editor").touch()
            assert done.wait(3), "Owned editor fixture did not close"


def key(process):
    process.stdin.write(b"key\n")
    process.stdin.flush()
    assert process.wait(timeout=5) == 37


@pytest.mark.parametrize("mode", ["windows-console", "windows-console-allocate"])
def test_dialog_close_ends_visible_console_but_leaves_completed_log_open(launch_notice, mode):
    process, directory = launch_notice(mode)
    notice = marker(directory, "helper")
    assert marker(directory, "console-visible")["visible"]
    user = visible(notice)
    text = ""
    for identifier in (1001, 1002):
        control = user.GetDlgItem(notice["window"], identifier)
        # WM_GETTEXT is marshalled across these owned processes; GetWindowText
        # cannot read another process's edit control contents.
        value = c.create_unicode_buffer(user.SendMessageW(control, 0xE, 0, 0) + 1)
        user.SendMessageW(control, 0xD, len(value), c.addressof(value))
        text += value.value
    log = directory / "modLauncher/logs/launcher.log"
    assert "while saving" in text and "Original saving failure" in text and str(log) in text
    with exit_wait(notice) as helper_done, editor_window(directory) as (editor, editor_done):
        assert editor["argv"] == [str(log)] and editor["path"] == str(log)
        assert "END OF ORIGINAL CAUSE" in editor["text"] and "Owned cleanup complete" in editor["text"]
        assert user.PostMessageW(notice["window"], 0x10, 0, 0)
        assert process.wait(timeout=5) == 37 and helper_done.wait(3)
        assert not editor_done.is_set()
        visible(editor)


def test_any_key_closes_only_its_own_dialog(launch_notice):
    first, first_dir = launch_notice("windows-key-first")
    second, second_dir = launch_notice("windows-key-second")
    first_notice, second_notice = marker(first_dir, "helper"), marker(second_dir, "helper")
    with exit_wait(first_notice) as done, editor_window(first_dir), editor_window(second_dir):
        key(first)
        assert done.wait(3) and second.poll() is None
        visible(second_notice)
        key(second)


def test_console_owner_exit_destroys_dialog_and_preserves_editor(launch_notice):
    process, directory = launch_notice("windows-owner")
    notice = marker(directory, "helper")
    with exit_wait(notice) as done, editor_window(directory) as (editor, editor_done):
        process.kill()  # Only the Popen-owned console process, never a discovered PID.
        process.wait(timeout=5)
        assert done.wait(3) and not editor_done.is_set()
        visible(editor)


def test_failed_native_editor_keeps_dialog_and_original_log(launch_notice):
    process, directory = launch_notice("windows-editor-failure")
    assert marker(directory, "editor-failed")["failed"]
    log = directory / "modLauncher/logs/launcher.log"
    wait_for(lambda: "PRESENTATION ERROR" in log.read_text(encoding="utf-8"))
    assert "END OF ORIGINAL CAUSE" in log.read_text(encoding="utf-8")
    notice = marker(directory, "helper")
    user = visible(notice)
    assert process.poll() is None
    assert user.PostMessageW(notice["window"], 0x10, 0, 0)
    assert process.wait(timeout=5) == 37


def test_failed_native_dialog_keeps_console_and_opens_log(launch_notice):
    process, directory = launch_notice("windows-dialog-failure")
    assert marker(directory, "dialog-failed")["failed"]
    with editor_window(directory) as (editor, done):
        log = directory / "modLauncher/logs/launcher.log"
        wait_for(lambda: "PRESENTATION ERROR" in log.read_text(encoding="utf-8"))
        assert "END OF ORIGINAL CAUSE" in log.read_text(encoding="utf-8")
        assert process.poll() is None
        key(process)
        assert not done.is_set()
        visible(editor)
