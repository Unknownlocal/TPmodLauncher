# SPDX-License-Identifier: MPL-2.0
"""Check complete fatal logging independently of packages and presentation."""

import json
from types import SimpleNamespace

import pytest

from launcher import entry


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
