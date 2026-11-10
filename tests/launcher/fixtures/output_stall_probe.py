# SPDX-License-Identifier: MPL-2.0
"""Exercise interpreter exit with an actually blocked owned output worker."""

import json
from pathlib import Path
import sys
import threading

project, directory, blocked = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(project))
from launcher.diagnostics import core

entered = threading.Event()
if blocked == "file":
    original = core.LogOutput.file_type
    class StalledFile(original):
        def write(self, text):
            entered.set()
            threading.Event().wait()
    core.LogOutput.file_type = StalledFile

path, lock = directory / "launcher.log", directory / "controller.lock"
outcome = {}
with core.log_ownership(lock):
    with core.reporter(path, outcome=outcome) as logger:
        # More than a pipe's capacity: the parent deliberately never reads stderr.
        logger.error("Owned complete record " + "x" * (256 * 1024) + " END")
        if blocked == "file":
            assert entered.wait(2)
            assert not core.sync_log(logger, timeout=.05)
        else:
            assert core.sync_log(logger)
            assert " END" in path.read_text(encoding="utf-8")
retained = False
if blocked == "file":
    try:
        with core.log_ownership(lock):
            raise AssertionError("A stalled writer released the launch lock")
    except RuntimeError:
        retained = True
(directory / "finished.json").write_text(json.dumps({"lock_retained": retained, "outcome": outcome}), encoding="utf-8")
