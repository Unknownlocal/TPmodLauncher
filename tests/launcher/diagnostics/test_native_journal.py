# SPDX-License-Identifier: MPL-2.0
"""Recover real subprocess failure before handling its queued process exit."""

from contextlib import closing
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from tests.launcher.fixtures.log_support import read_log

from launcher.diagnostics import handle_error, reporter
from launcher.diagnostics.activity_journal import ActivityJournal
from launcher.diagnostics.reporting_receiver import ReportingReceiver
from launcher.game_bridge import Bridge
from launcher.process_lifecycle import Controller, LaunchError
from tests.controller_device import rig


@pytest.mark.skipif(sys.platform != "win32", reason="Frozen Windows terminal handoff")
@pytest.mark.parametrize("mode", ["accepted", "closed", "formatter"])
def test_frozen_fatal_handoff_preserves_native_cleanup_and_requires_successful_reporting(tmp_path, mode):
    root = Path(__file__).resolve().parents[3]
    output = tmp_path / mode
    output.mkdir()
    journal = ActivityJournal(output / "activity.jsonl")
    journal.create()
    child = root / "tests/launcher/fixtures/fatal_handoff.py"
    executable = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen([executable, "-B", str(child), str(root), str(output), mode],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    receiver = ReportingReceiver(process.pid, "owned-final-handoff", lambda *args: None)
    receiver.start()
    try:
        _, stderr = process.communicate(json.dumps(receiver.endpoint) + "\n", timeout=10)
        assert process.returncode == 1 and "Owned fatal handoff FULL END" in stderr
        assert (output / "native-finally").exists() and (output / "finalised").exists()
        assert "Owned fatal handoff FULL END" in (output / "native-crash.txt").read_text(encoding="utf-8")
        assert (output / "bootloader-returned").exists() == (mode != "accepted")
        records = journal.read_available()
        assert any(row.get("fatal") and "Owned fatal handoff FULL END" in row.get("traceback", "")
                   for row in records) == (mode == "accepted")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        receiver.close(drain=receiver.connected.is_set())


@pytest.mark.parametrize("mode", ["normal", "abrupt", "ordinary"])
def test_final_restart_evidence_distinguishes_ordinary_exit_and_uncertain_loss(rig, tmp_path, mode):
    root = Path(__file__).resolve().parents[3]
    journal = ActivityJournal(tmp_path / "restart.jsonl")
    journal.create()
    child = tmp_path / "owned_restart.py"
    child.write_text('''import sys, json, os, threading
from itertools import count
from types import ModuleType, SimpleNamespace
from typing import Optional
sys.path.insert(0, sys.argv[1])
from hook_manager.native_readiness import NativeReadiness
from hook_manager.native_activity import NativeActivity
from hook_manager.native_restart import NativeRestart
from hook_manager.native_events import NativeEvents
from launcher.diagnostics.activity_journal import ActivityJournal
journal=ActivityJournal(sys.argv[2])
activity=NativeActivity(NativeReadiness(),journal,json.loads(sys.stdin.readline()))
def relaunch_application(*, series_mode: Optional[str] = None) -> None:
 if sys.argv[3] == 'abrupt': os._exit(0)
 raise SystemExit
for name in ('ui.screens.options_screen','ui.screens.season_pack_screen'):
 module=ModuleType(name)
 module.relaunch_application=relaunch_application
 sys.modules[name]=module
events=NativeEvents.__new__(NativeEvents)
events.host=SimpleNamespace(activity=activity)
events.active,events.closed,events.cache,events._sequence=True,False,{},count(1)
restart=NativeRestart(events)
events.publish('restart-observation',restart.status())
assert activity.writer.wait_idle(2)
assert sys.stdin.readline().strip() == 'go'
if sys.argv[3] == 'abrupt':
 entered=threading.Event()
 def stalled(rows):
  entered.set()
  threading.Event().wait(30)
 journal.append_records=stalled
 activity.submit_log({'level':'INFO','detail':'Owned stalled writer'})
 assert entered.wait(2)
if sys.argv[3] != 'ordinary':
 sys.modules['ui.screens.season_pack_screen'].relaunch_application(series_mode='oval')
''', encoding="utf-8")
    executable = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen([executable, "-B", str(child), str(root), str(journal.path),
                                mode],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    receiver = ReportingReceiver(process.pid, "owned-restart", lambda *args: None)
    try:
        receiver.start()
        process.stdin.write(json.dumps(receiver.endpoint) + "\n")
        process.stdin.flush()
        assert receiver.connected.wait(2)
        process.stdin.write("go\n")
        process.stdin.flush()
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert receiver.finished.wait(2)
        assert receiver.confirmed is (mode != "abrupt")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        receiver.close(drain=receiver.connected.is_set())
    device, controller, messages, now = rig
    controller.start()
    session = controller.sessions[10]
    session.create_script = lambda *args, **kwargs: SimpleNamespace(on=lambda *args: None)
    bridge = Bridge(None, session, "", lambda *args: None, journal, receiver)
    controller.bridges[10] = bridge
    try:
        # No journal notification is dispatched: only process exit triggers recovery.
        device.exit(10)
        assert controller.poll() is (mode == "ordinary")
        assert bridge.restart_observation["groups"] == {"restart.settings": "active", "restart.mod-pack": "active"}
        if mode == "abrupt":
            assert bridge.restart_attempt is None and journal.retained
        elif mode == "ordinary":
            assert bridge.restart_attempt is None and bridge.restart_observation["final"] is True
            assert not any("possible replacement" in message for message in messages)
        else:
            assert bridge.restart_attempt == {"attempt": 1, "source": "mod-pack", "series": "oval", "state": "committed"}
            assert sum("NATIVE RESTART:" in message for message in messages) == 2
            assert not journal.path.exists()
        # Only positive restart intent or uncertain evidence retains the grace.
        now[0] = controller.restart_grace
        assert controller.poll() and not controller.stopping
    finally:
        controller.close()


def test_immediate_native_exit_keeps_failure_and_saving_context(rig, tmp_path):
    root = Path(__file__).resolve().parents[3]
    journal = ActivityJournal(tmp_path / "native.jsonl")
    journal.create()
    child = tmp_path / "owned_native.py"
    child.write_text('''import sys, json
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
activity=NativeActivity(readiness,ActivityJournal(sys.argv[2]),json.loads(sys.stdin.readline()))
State().save_season(manual=True)
''', encoding="utf-8")
    notices = []
    # The Windows venv executable redirects to a different Python PID. This
    # stdlib-only child uses the base interpreter so its handle pins that PID.
    executable = getattr(sys, "_base_executable", sys.executable)
    process = subprocess.Popen([executable, "-B", str(child), str(root), str(journal.path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    receiver = None
    try:
        receiver = ReportingReceiver(process.pid, "owned-fatal-child", lambda event, detail: notices.append(
            {"type": "send", "payload": {"event": event, "detail": detail}}))
        receiver.start()
        _, stderr = process.communicate(json.dumps(receiver.endpoint) + "\n", timeout=10)
        assert process.returncode == 1 and "FULL EXIT END" in stderr
        assert receiver.finished.wait(2) and receiver.confirmed
        assert any(message["payload"]["event"] == "journal-ready" for message in notices)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=3)
        if receiver is not None:
            receiver.close(drain=receiver.connected.is_set())
    device, _, _, _ = rig
    outcome, path = {}, tmp_path / "launcher.log"
    tracked, requested = [], threading.Event()
    shutdown = SimpleNamespace(requested=requested, track=tracked.append, request=requested.set)
    with reporter(path, outcome=outcome) as logger, closing(Controller(device, [device.game], logger, shutdown=shutdown)) as controller:
        controller.start()
        session = controller.sessions[10]
        session.create_script = lambda *args, **kwargs: SimpleNamespace(on=lambda *args: None)
        controller.bridges[10] = Bridge(None, session, "", lambda *args: None, journal, receiver)
        for notice in notices:
            controller.events.put(("bridge", (10, session, notice)))
        device.child(20, device.game)
        unrelated = tmp_path / "unrelated-helper.exe"
        unrelated.write_bytes(b"owned fixture, never executed")
        device.child(30, unrelated)
        session.is_detached = True
        controller.events.put(("detached", (10, session, "process-terminated")))
        with pytest.raises(LaunchError) as failure:
            controller.poll()
        handle_error(logger, failure.value, stage="controller", shutdown=shutdown)
    text = read_log(path)
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
    assert outcome["failure"] is not None and detail in read_log(path)
    assert "Earlier complete native error" in read_log(path)
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
