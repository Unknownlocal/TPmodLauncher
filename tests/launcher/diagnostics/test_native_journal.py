# SPDX-License-Identifier: MPL-2.0
"""Recover real subprocess failure before handling its queued process exit."""

from contextlib import closing
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from launcher.diagnostics import handle_error, reporter
from launcher.diagnostics.activity_journal import ActivityJournal
from launcher.process_lifecycle import Controller, LaunchError
from tests.controller_device import rig


def test_immediate_native_exit_keeps_failure_and_saving_context(rig, tmp_path):
    root = Path(__file__).resolve().parents[3]
    journal = ActivityJournal(tmp_path / "native.jsonl")
    journal.create()
    child = tmp_path / "owned_native.py"
    child.write_text('''import sys
from types import ModuleType
sys.path.insert(0, sys.argv[1])
from hook_manager.native_readiness import NativeReadiness
from hook_manager.native_activity import NativeActivity
from launcher.diagnostics.activity_journal import ActivityJournal
class State:
 def save_season(self, *, manual=False):
  raise OSError('Owned immediate save failure ' + 'x'*5000 + ' FULL EXIT END')
 def advance_week(self, off_week=False, *, persist=True): pass
 def finalize_and_rollover_season(self): pass
module=ModuleType('game_state')
module.GameState=State
sys.modules[module.__name__]=module
readiness=NativeReadiness()
readiness._activity_status={'kind':'in_save','sequence':1,'generation':1}
activity=NativeActivity(readiness,ActivityJournal(sys.argv[2]))
State().save_season(manual=True)
''', encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(child), str(root), str(journal.path)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "FULL EXIT END" in result.stderr
    device, _, _, _ = rig
    outcome, path = {}, tmp_path / "launcher.log"
    tracked, requested = [], threading.Event()
    shutdown = SimpleNamespace(requested=requested, track=tracked.append, request=requested.set)
    with reporter(path, outcome=outcome) as logger, closing(Controller(device, [device.game], logger, shutdown=shutdown)) as controller:
        controller.start()
        controller.bridges[10] = SimpleNamespace(drain_activity=journal.read_available, journal=journal, close=lambda: None)
        device.child(20, device.game)
        unrelated = tmp_path / "unrelated-helper.exe"
        unrelated.write_bytes(b"owned fixture, never executed")
        device.child(30, unrelated)
        session = controller.sessions[10]
        session.is_detached = True
        controller.events.put(("detached", (10, session, "process-terminated")))
        with pytest.raises(LaunchError) as failure:
            controller.poll()
        handle_error(logger, failure.value, stage="controller", shutdown=shutdown)
    text = path.read_text(encoding="utf-8")
    assert outcome["failure"]["activity"]["kind"] == "saving" and outcome["log_written"]
    assert "FULL EXIT END" in outcome["failure"]["reason"]
    assert "FULL EXIT END" in text and "save_season" in text
    assert "NATIVE ACTIVITY STARTED" in text and "NATIVE ACTIVITY ENDED" in text
    assert not journal.path.exists()
    assert 20 in tracked and 30 not in tracked
    assert not device.pending and not controller.held


def test_interrupted_journal_write_retains_raw_failure_evidence(tmp_path):
    journal = ActivityJournal(tmp_path / "interrupted.jsonl")
    journal.create()
    journal.append_record({"level": "ERROR", "detail": "Earlier complete native error"})
    with journal.path.open("ab") as stream:
        stream.write(b'{"partial":')
    detail = "Owned raw save failure " + "x" * 5000 + " FULL RAW END"
    journal.append_record({"level": "ERROR", "detail": detail})
    path, outcome = tmp_path / "launcher.log", {}
    with reporter(path, outcome=outcome) as logger:
        try:
            journal.read_available()
        except ValueError as error:
            handle_error(logger, error)
    journal.remove()
    assert outcome["failure"] is not None and detail in path.read_text(encoding="utf-8")
    assert "Earlier complete native error" in path.read_text(encoding="utf-8")
    assert str(journal.path) in outcome["failure"]["reason"] and journal.path.exists()


def test_final_unfinished_record_waits_while_live_and_is_reported_at_disposal(tmp_path):
    journal = ActivityJournal(tmp_path / "unfinished.jsonl")
    journal.create()
    partial = b'{"detail":"Owned final partial crash evidence'
    journal.path.write_bytes(partial)
    assert journal.read_available() == []
    with pytest.raises(ValueError, match="Owned final partial crash evidence"):
        journal.remove()
    assert journal.path.read_bytes() == partial
