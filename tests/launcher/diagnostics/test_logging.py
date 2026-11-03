# SPDX-License-Identifier: MPL-2.0
"""Check complete fatal logging independently of packages and presentation."""

import json
from types import SimpleNamespace

import pytest

from launcher import entry


@pytest.mark.parametrize("level", ["verbose", "2.5", "-1"])
def test_invalid_log_level_is_rejected_before_opening_log(tmp_path, monkeypatch, level):
    monkeypatch.setattr(entry, "__file__", str(tmp_path / "launcher/entry.py"))
    with pytest.raises(SystemExit) as error:
        entry.main(["--log-level", level, "--check-only"])
    assert error.value.code == 2 and not (tmp_path / "logs/launcher.log").exists()


def test_startup_phases_retain_elapsed_wait_and_original_failure(tmp_path, capsys):
    import threading
    from launcher.diagnostics import core as diagnostics

    path = tmp_path / "launcher.log"
    cause = RuntimeError("Owned startup failure")
    # Numeric NOTSET includes all supported records, including startup DEBUG.
    with diagnostics.reporter(path, log_level=0) as logger:
        # An actual wait establishes elapsed rather than CPU time. The failed
        # phase must also retain its start and preserve the original exception.
        controller_sink = diagnostics.bind_reporter(logger, component="controller")
        with diagnostics.startup_step(controller_sink, "owned startup wait", pid=41):
            threading.Event().wait(.03)
        with pytest.raises(RuntimeError) as raised:
            with diagnostics.startup_step(logger, "owned failed phase"):
                raise cause
        assert raised.value is cause
        logger.info("Ordinary startup summary")
    records = [json.loads(line.partition("STARTUP ")[2].partition(" activity=")[0])
               for line in path.read_text().splitlines() if "STARTUP " in line]
    assert [(row["phase"], row["state"]) for row in records] == [
        ("owned startup wait", "started"), ("owned startup wait", "complete"),
        ("owned failed phase", "started"), ("owned failed phase", "failed")]
    assert records[1]["seconds"] >= .025
    assert records[3]["seconds"] >= 0
    assert "DEBUG modLauncher.controller pid=41 STARTUP" in path.read_text()
    console = capsys.readouterr().err
    assert "Ordinary startup summary" in console and console == path.read_text()


def test_nested_logging_preserves_levels_context_and_full_remote_details(tmp_path, capsys):
    from launcher.diagnostics import core as diagnostics

    path = tmp_path / "launcher.log"
    activity = {"kind": "saving", "description": "while saving"}
    with diagnostics.reporter(path, log_level="DEBUG") as logger:
        bound = diagnostics.bind_reporter(logger, component="controller", pid=41)
        diagnostics.write_log(bound, "Owned debug detail", level="DEBUG", scope="startup")
        diagnostics.write_log(bound, {"level": "WARNING", "detail": "Owned warning", "activity": activity})
        diagnostics.write_log(diagnostics.bind_reporter(bound, component="mods"),
                              {"level": "ERROR", "detail": "Owned remote failure", "traceback": "Full remote cause\nEnd of remote cause"})
        defaults = diagnostics.bind_reporter(bound, level="WARNING", error=ValueError("Bound default cause"))
        diagnostics.write_log(defaults, "Inherited warning")
        diagnostics.write_log(defaults, "Explicit info", level="INFO", error=None)
    text = path.read_text()
    assert "DEBUG modLauncher.controller pid=41 scope=startup Owned debug detail" in text
    warning = next(line for line in text.splitlines() if "Owned warning" in line)
    assert "WARNING modLauncher.controller pid=41" in warning
    assert json.loads(warning.partition(" activity=")[2]) == activity
    assert text.count("ERROR modLauncher.mods pid=41 Owned remote failure") == 1
    assert text.count("Full remote cause\nEnd of remote cause") == 1
    assert "WARNING modLauncher.controller pid=41 Inherited warning" in text
    assert "INFO modLauncher.controller pid=41 Explicit info" in text
    assert text.count("Bound default cause") == 1
    console = capsys.readouterr().err
    assert console == text


@pytest.mark.parametrize("level,visible", [
    (1, ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
    (2, ["INFO", "WARNING", "ERROR", "CRITICAL"]),
    (3, ["WARNING", "ERROR", "CRITICAL"]),
    (4, ["ERROR", "CRITICAL"]),
    (5, ["CRITICAL"]),
    (40, ["ERROR", "CRITICAL"]),
])
def test_numbered_threshold_filters_console_and_file_equally(tmp_path, capsys, level, visible):
    from launcher.diagnostics import core as diagnostics

    path = tmp_path / "launcher.log"
    with diagnostics.reporter(path, log_level=level) as logger:
        for severity in diagnostics.LEVELS:
            diagnostics.write_log(logger, "Selected " + severity, level=severity)
    text = path.read_text()
    assert [severity for severity in diagnostics.LEVELS if "Selected " + severity in text] == visible
    assert capsys.readouterr().err == text


def test_critical_threshold_still_retains_complete_fatal_error_at_original_level(tmp_path, capsys):
    from launcher.diagnostics import core as diagnostics

    path, outcome = tmp_path / "launcher.log", {}
    with diagnostics.reporter(path, outcome=outcome, log_level="CRITICAL") as logger:
        logger.info("Routine progress filtered from file")
        try:
            raise OSError("Original full nested cause")
        except OSError as cause:
            error = RuntimeError("Original fatal failure")
            error.__cause__ = cause
            diagnostics.handle_error(logger, error)
    text = path.read_text()
    assert "Routine progress" not in text
    assert "ERROR modLauncher.runtime" in text and "Original full nested cause" in text
    assert "Original fatal failure" in text and "ERROR modLauncher LAUNCH FAILURE DETAILS" in text
    assert outcome["log_written"] and outcome["failure"]["reason"] == "Original fatal failure"
    assert capsys.readouterr().err == text


@pytest.mark.parametrize("late_failure", ["sync", "close"])
def test_final_log_failure_does_not_claim_its_own_success(tmp_path, monkeypatch, capsys, late_failure):
    from launcher.diagnostics import core as diagnostics

    path = tmp_path / "launcher.log"
    outcome = {}
    if late_failure == "sync":
        sync = diagnostics.os.fsync
        def final_sync(fd):
            if "LAUNCH FAILURE DETAILS" in path.read_text(encoding="utf-8"):
                raise OSError("Final filesystem sync failed")
            sync(fd)
        monkeypatch.setattr(diagnostics.os, "fsync", final_sync)
    else:
        close = diagnostics.LogFileHandler.close
        def final_close(handler):
            close(handler)
            raise OSError("Final file close failed")
        monkeypatch.setattr(diagnostics.LogFileHandler, "close", final_close)
    with diagnostics.reporter(path, outcome=outcome) as logger:
        diagnostics.handle_error(logger, RuntimeError("Original launcher failure"), stage="controller")
    text = path.read_text(encoding="utf-8")
    saved = json.loads(text.rpartition("LAUNCH FAILURE DETAILS ")[2])
    assert saved["failure"]["reason"] == outcome["failure"]["reason"] == "Original launcher failure"
    assert "log_written" not in saved and "log_error" not in saved
    assert not outcome["log_written"] and "Final" in outcome["log_error"]
    console = capsys.readouterr().err
    assert "Original launcher failure" in console and "Launcher log could not be completed" in console


def test_log_close_failure_alone_keeps_its_activity(tmp_path, monkeypatch):
    from launcher.diagnostics import core as diagnostics

    close = diagnostics.LogFileHandler.close
    def failed_close(handler):
        close(handler)
        raise OSError("Log close failed after normal operation")
    monkeypatch.setattr(diagnostics.LogFileHandler, "close", failed_close)
    outcome = {}
    with diagnostics.reporter(tmp_path / "launcher.log", outcome=outcome) as logger:
        logger.launch_activity.attach(41)
        logger.launch_activity.observe(41, {"kind": "in_save", "sequence": 1, "generation": 1})
    assert not outcome["log_written"] and "Log close failed" in outcome["log_error"]
    assert outcome["failure"]["stage"] == "logging"
    assert outcome["failure"]["activity"]["kind"] == "in_save"


@pytest.mark.parametrize("file_failure", [None, "open", "write", "sync"])
def test_dependency_failure_is_complete_without_packages_or_presentation(tmp_path, monkeypatch, capsys, file_failure):
    import logging

    root = tmp_path / "modLauncher"
    monkeypatch.setattr(entry, "__file__", str(root / "launcher/entry.py"))
    monkeypatch.setattr(logging, "raiseExceptions", False)
    cause = "Native dependency cause " + "x" * 5000 + " END OF CAUSE"
    def broken_dependencies(root):
        try:
            raise OSError(cause)
        except OSError as error:
            raise entry._dependencies.DependencyError("Dependencies could not load") from error
    monkeypatch.setattr(entry._dependencies, "import_dependencies", broken_dependencies)
    def unavailable(*args):
        raise OSError("simulated file " + file_failure + " failure")
    if file_failure == "sync":
        monkeypatch.setattr(entry._diagnostics.os, "fsync", unavailable)
    elif file_failure == "open":
        root.mkdir()
        (root / "logs").write_text("Keep this existing file", encoding="utf-8")
    elif file_failure == "write":
        handler = entry._diagnostics.LogFileHandler
        open_stream = handler._open
        def unwritable(instance):
            stream = open_stream(instance)
            return SimpleNamespace(write=unavailable, flush=stream.flush, close=stream.close, fileno=stream.fileno)
        monkeypatch.setattr(handler, "_open", unwritable)
    outcome = {}
    assert entry.main(["--check-only"], outcome=outcome) == 1
    console = capsys.readouterr().err
    assert cause in console and "direct cause" in console and "Dependencies could not load" in console
    assert outcome["failure"]["reason"] == "Dependencies could not load"
    assert outcome["failure"]["stage"] == "dependency_verification"
    assert outcome["failure"]["activity"]["kind"] == "launcher_startup"
    text = (root / "logs/launcher.log").read_text(encoding="utf-8") if file_failure != "open" else ""
    if file_failure:
        assert not outcome["log_written"] and outcome["log_error"]
        assert "Launcher log could not be completed" in console
        if file_failure == "open":
            assert (root / "logs").read_text(encoding="utf-8") == "Keep this existing file"
    else:
        assert cause in text and "direct cause" in text and outcome["log_written"]
        assert json.loads(text.rpartition("LAUNCH FAILURE DETAILS ")[2])["failure"] == outcome["failure"]
