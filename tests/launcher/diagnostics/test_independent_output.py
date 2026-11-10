# SPDX-License-Identifier: MPL-2.0
"""Check independent delivery, ownership and bounded process exit."""

import io
import json
import linecache
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from launcher.diagnostics import core


@pytest.mark.parametrize("blocked", ["console", "file"])
def test_a_stalled_destination_does_not_block_the_other(tmp_path, monkeypatch, blocked):
    entered, release, displayed = threading.Event(), threading.Event(), threading.Event()
    console = io.StringIO()
    class Console:
        def write(self, text):
            if blocked == "console":
                entered.set()
                assert release.wait(3)
            console.write(text)
            if "Original complete cause" in text:
                displayed.set()
        def flush(self):
            pass
    original = core.LogOutput.file_type
    class File(original):
        def write(self, text):
            if blocked == "file":
                entered.set()
                assert release.wait(3)
            super().write(text)
    monkeypatch.setattr(core.LogOutput, "file_type", File)
    monkeypatch.setattr(sys, "stderr", Console())
    outcome, path = {}, tmp_path / "launcher.log"
    with core.reporter(path, outcome=outcome) as logger:
        try:
            logger.info("Owned in-flight record")
            assert entered.wait(1)
            error = RuntimeError("Original complete cause " + "x" * 7000 + " END OF CAUSE")
            core.write_log(logger, "Owned fatal diagnostic", level="ERROR", error=error, fatal=True)
            if blocked == "console":
                assert core.sync_log(logger)
                assert "END OF CAUSE" in path.read_text()
                assert not displayed.is_set()
            else:
                assert displayed.wait(1)
                assert "END OF CAUSE" in console.getvalue()
                assert not core.sync_log(logger, timeout=.03)
                assert not outcome["log_written"]
        finally:
            release.set()
    assert outcome["log_written"] == (blocked == "console")


def test_filtering_precedes_rendering_and_submission_freezes_activity(tmp_path, capsys):
    class Unrenderable:
        def __str__(self):
            raise AssertionError("A suppressed message was rendered")
    path = tmp_path / "launcher.log"
    activity = {"kind": "saving", "description": "Original saving activity"}
    with core.reporter(path, log_level="INFO") as logger:
        core.write_log(logger, Unrenderable(), level="DEBUG")
        logger.debug("Suppressed %s", Unrenderable())
        core.write_log(logger, {"detail": "Owned immutable record", "level": "INFO", "activity": activity})
        activity["description"] = "Changed after submission"
    assert capsys.readouterr().err == path.read_text()
    assert "Original saving activity" in path.read_text() and "Changed after submission" not in path.read_text()


def test_only_the_stalled_console_overflows_and_complete_errors_survive(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    console = io.StringIO()
    class Console:
        def write(self, text):
            entered.set()
            assert release.wait(4)
            console.write(text)
        def flush(self):
            pass
    monkeypatch.setattr(sys, "stderr", Console())
    path = tmp_path / "launcher.log"
    with core.reporter(path) as logger:
        try:
            logger.info("Owned blocked console")
            assert entered.wait(1)
            # An admitted INFO record with a traceback is protected too.
            core.write_log(logger, "Owned complete error", error=RuntimeError("Unabridged cause " + "x" * 7000 + " FULL END"))
            for index in range(1000):
                logger.info("Owned routine %s", index)
                if index % 64 == 0:
                    assert core.sync_log(logger)
            assert core.sync_log(logger)
            assert path.read_text().count("Owned routine ") == 1000
        finally:
            release.set()
    text = console.getvalue()
    assert "console output overflow: dropped" in text
    assert " FULL END" in text and "Owned routine 999" in text
    assert "output overflow" not in path.read_text()


def test_late_file_failure_keeps_the_failed_records_activity(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = core.LogOutput.file_type
    class File(original):
        def write(self, text):
            if not entered.is_set():
                entered.set()
                assert release.wait(2)
                try:
                    raise OSError("Original output cause")
                except OSError as error:
                    raise OSError("Owned delayed saving write failed") from error
            super().write(text)
    monkeypatch.setattr(core.LogOutput, "file_type", File)
    def source_lookup(*args, **kwargs):
        raise AssertionError("An output error looked up source files")
    monkeypatch.setattr(linecache, "checkcache", source_lookup)
    monkeypatch.setattr(linecache, "getline", source_lookup)
    outcome = {}
    with core.reporter(tmp_path / "launcher.log", outcome=outcome) as logger:
        try:
            with core.activity_scope(logger, "saving"):
                logger.info("Owned saving record")
            assert entered.wait(1)
            with core.activity_scope(logger, "menu"):
                logger.info("Later unrelated menu record")
        finally:
            release.set()
        assert not core.sync_log(logger)
    assert outcome["failure"]["activity"]["kind"] == "saving"
    assert "Original output cause" in outcome["log_error"] and "direct cause" in outcome["log_error"]


def test_redirecting_console_to_null_keeps_a_successful_log(tmp_path, monkeypatch):
    outcome, path = {}, tmp_path / "launcher.log"
    with open(os.devnull, "w", encoding="utf-8") as console:
        monkeypatch.setattr(sys, "stderr", console)
        with core.reporter(path, outcome=outcome) as logger:
            logger.info("Owned redirected Unicode output: é")
    assert outcome["log_written"] and "console_error" not in outcome
    assert "Owned redirected Unicode output: é" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("blocked", ["console", "file"])
def test_blocked_raw_output_cannot_hold_interpreter_exit_or_release_a_live_file_lease(tmp_path, blocked):
    root = Path(__file__).resolve().parents[3]
    command = [sys._base_executable, "-I", "-B", str(root / "tests/launcher/fixtures/output_stall_probe.py"),
               str(root), str(tmp_path), blocked]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        # Do not communicate(): reading stderr here would unblock the console.
        assert process.wait(timeout=5) == 0
        result = json.loads((tmp_path / "finished.json").read_text())
        assert result["lock_retained"] == (blocked == "file")
        assert result["outcome"]["log_written"] == (blocked == "console")
        # The OS releases the retained lock when the owning process exits.
        with core.log_ownership(tmp_path / "controller.lock"):
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stderr.close()
