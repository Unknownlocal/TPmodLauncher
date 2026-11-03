# SPDX-License-Identifier: MPL-2.0
"""Own disposable failure sessions and their independent editor fixtures."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROBE = ROOT / "tests/launcher/fixtures/failure_session_probe.py"


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Owned failure fixture did not reach its checkpoint")
        time.sleep(.01)


@pytest.fixture
def launch_notice(tmp_path):
    processes, streams = [], []
    def launch(mode):
        directory = tmp_path / (mode + " \u2013 \u00e9 & space" if mode.startswith("windows-") else mode)
        directory.mkdir()
        stderr = (directory / "console.txt").open("w", encoding="utf-8")
        streams.append(stderr)
        options = {}
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            options.update(creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
            if mode == "windows-console-allocate":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen([sys._base_executable, "-I", "-S", "-B", str(PROBE), str(ROOT), str(directory), mode, sys.executable],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr, **options)
        processes.append(process)
        wait_for(lambda: (directory / "helper.json").exists() or process.poll() is not None
                 or mode == "missing" and "Presentation helper failed" in (directory / "console.txt").read_text(encoding="utf-8")
                 or mode == "timeout" and "Error dialog startup timed out" in (directory / "console.txt").read_text(encoding="utf-8"))
        code = process.poll()
        assert code is None or mode == "dialog" and code == 37, (directory / "console.txt").read_text(encoding="utf-8")
        return process, directory
    yield launch
    for directory in tmp_path.iterdir():
        (directory / "close-editor").touch()
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
    for stream in streams:
        stream.close()


