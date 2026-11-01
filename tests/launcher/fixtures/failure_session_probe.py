# SPDX-License-Identifier: MPL-2.0
"""Run a complete fatal entry/session using only disposable files and processes."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import time

project, output, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = load("owned_entry", project / "launcher/entry.py")
session_module = load("owned_session", project / "launcher/diagnostics/failure_session.py")
child_type = load("owned_child", project / "launcher/diagnostics/presentation_child.py").PresentationChild
# Relocate only the owned log/lock; all helper source remains trusted project code.
entry.__file__ = str(output / "modLauncher/launcher/entry.py")
outcome = {}


def fail(root, arguments, command, logger, lock_path, log_path):
    with entry._diagnostics.activity_scope(logger, "saving"):
        try:
            raise OSError("Original nested cause " + "x" * 7000 + " END OF ORIGINAL CAUSE")
        except OSError as cause:
            error = RuntimeError("Original saving failure")
            error.__cause__ = cause
            entry._diagnostics.handle_error(logger, error, stage="controller")
    logger.info("Owned cleanup complete")
    outcome["cleanup"], outcome["game_close"] = "complete", "complete"
    return 37


entry._run = fail
if mode == "default":
    # Exercise a Windows venv redirector distinct from the actual owned runtime.
    sys.executable = sys.argv[4]
terminal = None
if mode in ("failure", "missing"):
    terminal = open(os.devnull, "r")
elif os.name == "nt":
    import ctypes as c
    from ctypes import wintypes as w
    import msvcrt
    import struct

    terminal = open("CONIN$", "r")
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.WriteConsoleInputW.argtypes = (w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD))
    kernel.WriteConsoleInputW.restype = w.BOOL
    kernel.GenerateConsoleCtrlEvent.argtypes = (w.DWORD, w.DWORD)
    kernel.GenerateConsoleCtrlEvent.restype = w.BOOL
    kernel.SetConsoleCtrlHandler.argtypes = (c.c_void_p, w.BOOL)
    kernel.SetConsoleCtrlHandler.restype = w.BOOL
    assert kernel.SetConsoleCtrlHandler(None, False)  # Only the fixture's inherited ignore flag.
    kernel.GetConsoleMode.argtypes = (w.HANDLE, c.POINTER(w.DWORD))
    kernel.GetConsoleMode.restype = w.BOOL
    original_mode = w.DWORD()
    assert kernel.GetConsoleMode(msvcrt.get_osfhandle(terminal.fileno()), c.byref(original_mode))

    def control_key():
        assert kernel.GenerateConsoleCtrlEvent(0, 0)  # Only this fixture's private console.

    def key():
        mode = w.DWORD()
        assert kernel.GetConsoleMode(msvcrt.get_osfhandle(terminal.fileno()), c.byref(mode)) and not mode.value & 1
        record = c.create_string_buffer(struct.pack("<H2xIHHHHI", 1, 1, 1, 88, 0, ord("x"), 0))
        count = w.DWORD()
        assert kernel.WriteConsoleInputW(msvcrt.get_osfhandle(terminal.fileno()), record, 1, c.byref(count))
else:
    import pty
    import termios

    master, slave = pty.openpty()
    terminal = os.fdopen(slave, "r")
    original_mode = termios.tcgetattr(terminal.fileno())
    def key():
        os.write(master, b"x")
    def control_key():
        import signal
        os.kill(os.getpid(), signal.SIGINT)


def present(value, *, owned_log, wait):
    assert value["cleanup"] == "complete" and value["game_close"] == "complete"
    assert "Owned cleanup complete" in Path(value["log_path"]).read_text(encoding="utf-8")
    try:
        with entry._diagnostics.log_ownership(Path(entry.__file__).parents[1] / ".state/controller.lock"):
            raise AssertionError("Log ownership was released before presentation")
    except RuntimeError:
        pass

    def child_factory(notice, submit):
        command = [sys._base_executable, "-I", "-S", "-B", str(Path(__file__).with_name("failure_backend_probe.py")),
                   str(project / "launcher/diagnostics"), str(output), mode]
        if mode == "missing":
            command[0] = str(output / "missing-helper-runtime")
        return child_type(command, notice, submit, startup_timeout=.2 if mode == "timeout" else 5)

    session = session_module.FailureSession(value, owned_log=owned_log, input_stream=terminal,
                                            child_factory=None if mode == "default" else child_factory)
    def control():
        if mode == "default":
            while session.child is None or session.child.process is None:
                time.sleep(.01)
            (output / "helper.json").write_text(json.dumps({"pid": session.child.process.pid}), encoding="utf-8")
        for line in sys.stdin:
            if line.strip() == "key":
                key()
            elif line.strip() == "ctrl":
                control_key()
            elif line.strip() == "dismiss":
                session.dismiss()
    threading.Thread(target=control, daemon=True).start()
    session.run()
    assert session.outcome["failure"] == value["failure"]
    restored = True
    if mode not in ("failure", "missing"):
        if os.name == "nt":
            current_mode = w.DWORD()
            assert kernel.GetConsoleMode(msvcrt.get_osfhandle(terminal.fileno()), c.byref(current_mode))
            restored = current_mode.value == original_mode.value
        else:
            restored = termios.tcgetattr(terminal.fileno()) == original_mode
    (output / "finished.json").write_text(json.dumps({"failure": value["failure"], "terminal_restored": restored}), encoding="utf-8")


try:
    code = entry.main(["--", "owned-fixture-only"], outcome=outcome, presentation=present)
finally:
    terminal.close()
    if os.name != "nt" and mode not in ("failure", "missing"):
        os.close(master)
raise SystemExit(code)
