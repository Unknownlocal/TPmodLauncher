# SPDX-License-Identifier: MPL-2.0
"""Exercise native exit/cancellation waits on children owned by this test."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from launcher.process_exit import ProcessExit


@contextmanager
def child():
    # Windows venv launchers can redirect to another PID. Use the interpreter
    # itself so the Popen PID and the pinned OS process are the same owner.
    executable = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    process = subprocess.Popen([str(executable), "-I", "-S", "-c",
                                "import sys; sys.stdin.buffer.read(1)"], stdin=subprocess.PIPE)
    try:
        yield process, executable
    finally:
        process.stdin.close()
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=3)


def test_owned_exit_wakes_without_frida_and_cancellation_keeps_process_alive():
    received, completed = [], threading.Event()
    def exited(error):
        received.append(error)
        completed.set()
    with child() as (process, executable):
        cancelled = ProcessExit(process.pid, executable, exited)
        cancelled.start()
        cancelled.close()
        assert not completed.is_set() and process.poll() is None
        waiter = ProcessExit(process.pid, executable, exited)
        try:
            waiter.start()
            process.stdin.write(b"x")
            process.stdin.flush()
            assert completed.wait(3) and received == [None]
            assert process.wait(timeout=3) == 0
        finally:
            waiter.close()


def test_pin_rejects_wrong_executable_and_exit_during_attachment(tmp_path):
    with child() as (process, executable):
        with pytest.raises(RuntimeError, match="owned executable"):
            ProcessExit(process.pid, tmp_path / "foreign.exe", lambda _: None)
        waiter = ProcessExit(process.pid, executable, lambda _: pytest.fail("Unverified attachment completed"))
        try:
            process.stdin.write(b"x")
            process.stdin.flush()
            process.wait(timeout=3)
            with pytest.raises(ProcessLookupError, match="during attachment"):
                waiter.start()
        finally:
            waiter.close()
