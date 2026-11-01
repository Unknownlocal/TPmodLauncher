# SPDX-License-Identifier: MPL-2.0
"""Exercise whole week/save observation on owned state and real worker threads."""

import json
from types import ModuleType
import sys
import threading

import pytest

from hook_manager import diagnostics
from hook_manager.native_activity import NativeActivity
from hook_manager.native_readiness import NativeReadiness
from launcher.diagnostics import reporter, write_log
from tests.controller_device import rig


@pytest.fixture
def observed(monkeypatch, tmp_path):
    class State:
        def __init__(self):
            self.week, self.fail_save, self.fail_week = 0, False, False
            self.saved = tmp_path / "owned-save.json"
            self.during_save = lambda: None
        def save_season(self, *, manual=False):
            self.during_save()
            if self.fail_save:
                raise self.fail_save
            self.saved.write_text(json.dumps({"week": self.week, "manual": manual}))
            return str(self.saved)
        def advance_week(self, off_week=False, *, persist=True):
            if self.fail_week:
                raise self.fail_week
            self.week += 1
            if persist:
                self.save_season()
            return not off_week
        def finalize_and_rollover_season(self):
            return self.save_season()
    class Transition:
        def __init__(self, state):
            self.state, self.error = state, None
        def _start_background_week_advance(self):
            def work():
                try:
                    self.state.advance_week(True)
                except Exception as error:
                    self.error = error
            self.worker = threading.Thread(target=work)
            self.worker.start()
        def _finish_background_week_advance(self, *, show_review=True):
            self.worker.join(2)
            assert not self.worker.is_alive()
            if self.error:
                raise self.error
            return show_review
    originals = State.advance_week, State.save_season, Transition._start_background_week_advance
    for name, owner, cls in (("game_state", "GameState", State),
                             ("ui.week_advance_transition", "WeekAdvanceTransitionMixin", Transition)):
        module = ModuleType(name)
        setattr(module, owner, cls)
        monkeypatch.setitem(sys.modules, name, module)
    readiness = NativeReadiness()
    readiness._activity_status = {"kind": "in_save", "sequence": 1, "generation": 1}
    activity = NativeActivity(readiness)
    monkeypatch.setattr(diagnostics, "activity_provider", activity.current_activity)
    try:
        yield State, Transition, activity, readiness
    finally:
        activity.stop()
        assert activity.writer.finish(2)
    assert (State.advance_week, State.save_season, Transition._start_background_week_advance) == originals


def test_manual_save_and_nested_autosave_preserve_results_and_restore_week(observed):
    State, _, activity, _ = observed
    state, stages = State(), []
    state.during_save = lambda: stages.append(activity.current_activity())
    saved = state.save_season(manual=True)
    assert json.loads(state.saved.read_text()) == {"week": 0, "manual": True}
    assert saved == str(state.saved) and stages[-1]["kind"] == "saving"
    assert state.advance_week() is True
    assert json.loads(state.saved.read_text()) == {"week": 1, "manual": False}
    nested = stages[-1]
    assert activity.writer.wait_idle(2)
    parent = next(record["activity"] for record in activity.records
                  if record["detail"] == "NATIVE ACTIVITY STARTED" and record["activity"]["operation"] == nested["parent"])
    assert parent["kind"] == "week_progression"
    assert any(record["detail"] == "NATIVE ACTIVITY ENDED" and record["activity"]["kind"] == "week_progression"
               for record in activity.records)
    assert state.advance_week(off_week=True, persist=False) is False
    assert json.loads(state.saved.read_text()) == {"week": 1, "manual": False}
    assert activity.current_activity()["kind"] == "in_save"


def test_rollover_observation_coexists_with_later_native_caller_decorators(observed):
    from functools import wraps
    from hook_manager.callables import CallableTarget

    State, _, activity, _ = observed
    state, stages = State(), []
    installed = State.finalize_and_rollover_season
    target = CallableTarget("owned.rollover", "game_state", ("GameState",), "finalize_and_rollover_season", "(self)")
    target.resolve({})  # The existing callable adapter must accept this decorator.
    @wraps(installed)
    def caller(owner):
        stages.append("caller")
        return installed(owner)
    caller._tpm_hook_group = "owned.rollover"
    State.finalize_and_rollover_season = caller
    state.during_save = lambda: stages.append(activity.current_activity()["kind"])
    try:
        assert state.finalize_and_rollover_season() == str(state.saved)
        assert stages == ["caller", "saving"]
        assert activity.writer.wait_idle(2)
        assert any(row["activity"]["kind"] == "week_progression" for row in activity.records)
    finally:
        State.finalize_and_rollover_season = installed


@pytest.mark.parametrize("failure", ["fail_week", "fail_save"])
def test_worker_failure_keeps_original_scope_through_main_thread_reraise_and_logging(observed, tmp_path, failure):
    State, Transition, activity, _ = observed
    state, error = State(), RuntimeError("Owned failure " + "x" * 5000 + " FULL END")
    setattr(state, failure, error)
    transition = Transition(state)
    transition._start_background_week_advance()
    with pytest.raises(RuntimeError) as raised:
        transition._finish_background_week_advance()
    assert raised.value is error and transition.error is error
    kind = "saving" if failure == "fail_save" else "week_progression"
    record = diagnostics.build_log_record("ERROR", "Deferred worker failure", error=error)
    assert record["activity"]["kind"] == kind
    assert "FULL END" in record["traceback"]
    assert activity.writer.wait_idle(2)
    failures = [row for row in activity.records if row["level"] == "ERROR"]
    assert len(failures) == 1 and failures[0]["activity"]["kind"] == kind
    path = tmp_path / "launcher.log"
    with reporter(path) as logger:
        write_log(logger, record)
    text = path.read_text()
    assert kind in text and "FULL END" in text
    assert activity.current_activity()["kind"] == "in_save"


def test_worker_save_is_visible_to_host_but_not_unrelated_workers_or_new_careers(observed):
    State, Transition, activity, readiness = observed
    entered, release = threading.Event(), threading.Event()
    def saving():
        entered.set()
        assert release.wait(2)
    state = State()
    state.during_save = saving
    transition = Transition(state)
    transition._start_background_week_advance()
    try:
        assert entered.wait(2)
        assert activity.snapshot()["kind"] == "saving"
        assert activity.current_activity()["kind"] == "week_progression"
        other = []
        worker = threading.Thread(target=lambda: other.append(activity.current_activity()))
        worker.start()
        worker.join(2)
        assert not worker.is_alive() and other[0]["kind"] == "in_save"
        readiness._activity_status = {"kind": "menu", "sequence": 2, "generation": 2}
        assert activity.snapshot()["kind"] == "menu"
    finally:
        release.set()
        transition._finish_background_week_advance()
    assert activity.snapshot()["kind"] == "menu"


def test_new_week_root_uses_new_generation_while_old_transition_awaits_finish(observed):
    State, Transition, activity, readiness = observed
    state = State()
    old = Transition(state)
    old._start_background_week_advance()
    old.worker.join(2)
    readiness._activity_status = {"kind": "in_save", "generation": 3, "sequence": 3}
    new = Transition(state)
    new._start_background_week_advance()
    new.worker.join(2)
    assert not new.worker.is_alive()
    try:
        current = activity.snapshot()
        assert current["kind"] == "week_progression" and current["generation"] == 3
        assert current["parent"] is None
    finally:
        new._finish_background_week_advance()
        old._finish_background_week_advance()
    assert activity.current_activity()["kind"] == "in_save"


def test_failed_journal_keeps_exception_and_fatal_bridge_fallback(observed, rig, tmp_path, capsys):
    from contextlib import closing
    from launcher.diagnostics.activity_journal import ActivityJournal
    from launcher.process_lifecycle import LaunchError

    State, _, activity, _ = observed
    activity.journal = ActivityJournal(tmp_path / "unavailable.jsonl")
    state, error = State(), OSError("Owned journal fallback failure")
    state.fail_save = error
    with pytest.raises(OSError) as raised:
        state.save_season()
    assert raised.value is error
    activity._unhandled(type(error), error, error.__traceback__)
    assert "Owned journal fallback failure" in capsys.readouterr().err
    assert any("Native activity journal failed" in record["detail"] for record in activity.records)
    device, controller, _, _ = rig
    with closing(controller):
        controller.start()
        session = controller.sessions[10]
        for record in activity.records:
            controller.events.put(("bridge", (10, session, {"type": "send", "payload": {**record, "event": "native-activity"}})))
        with pytest.raises(LaunchError) as fatal:
            controller.poll()
        assert "Owned journal fallback failure" in str(fatal.value)
        assert fatal.value.launcher_activity["kind"] == "saving"
        assert controller.activity.snapshot(10)["kind"] == "in_save"
        # Late unload errors remain quiet during an otherwise successful cleanup.
        controller.events.put(("bridge", (10, session, {"type": "error", "description": "Late script unload"})))
    assert not controller.cleanup_failed


def test_blocked_journal_does_not_delay_gameplay_status_or_stop(observed, tmp_path, monkeypatch):
    from launcher.diagnostics.activity_journal import ActivityJournal

    State, _, activity, _ = observed
    journal = ActivityJournal(tmp_path / "shutdown.jsonl")
    journal.create()
    activity.journal = journal
    entered, release = threading.Event(), threading.Event()
    append = journal.append_records
    def delayed_error(records):
        if any(record["level"] == "ERROR" for record in records):
            entered.set()
            assert release.wait(2)
        append(records)
    monkeypatch.setattr(journal, "append_records", delayed_error)
    state, error = State(), OSError("Owned save failure during stop")
    state.fail_save = error
    errors = []
    def work():
        try:
            state.save_season()
        except OSError as failure:
            errors.append(failure)
    worker = threading.Thread(target=work)
    worker.start()
    assert entered.wait(2)
    try:
        # The save callback and another status read both finish while the writer
        # is still inside storage. Stop only signals; its acknowledgement waits.
        worker.join(1)
        assert not worker.is_alive() and errors == [error]
        assert state.advance_week(persist=False) is True
        assert activity.snapshot()["kind"] == "in_save"
        assert activity.stop() is False
        assert not activity.writer.finished.is_set()
    finally:
        release.set()
        worker.join(2)
    assert activity.writer.finish(2)
    assert activity.stop() is True
    records = journal.read_available()
    assert any(record["level"] == "ERROR" and "Owned save failure during stop" in record["traceback"] for record in records)
    journal.remove()
    assert not journal.path.exists() and not activity.records


def test_slow_traceback_source_lookup_runs_on_writer_not_game_thread(observed, monkeypatch):
    import linecache

    State, _, activity, _ = observed
    entered, release, readers = threading.Event(), threading.Event(), []
    linecache.getlines(__file__)  # Cached source makes Python's checkcache call os.stat.
    checkcache = linecache.checkcache
    def slow_source(filename=None):
        if filename is not None and filename.endswith("test_native_activity.py"):
            readers.append(threading.get_ident())
            entered.set()
            assert release.wait(2)
        return checkcache(filename)
    monkeypatch.setattr(linecache, "checkcache", slow_source)
    state, error = State(), OSError("Owned deferred source lookup FULL END")
    state.fail_save = error
    try:
        with pytest.raises(OSError) as raised:
            state.save_season()
        assert raised.value is error and entered.wait(2)
        assert state.advance_week(persist=False) is True
        assert activity.current_activity()["kind"] == "in_save"
    finally:
        release.set()
    assert activity.writer.wait_idle(2)
    assert readers and all(reader == activity.writer._thread.ident for reader in readers)
    failure = next(record for record in activity.records if record["level"] == "ERROR")
    assert "FULL END" in failure["traceback"] and "save_season" in failure["traceback"]


def test_deferred_error_format_keeps_causes_notes_group_members_and_syntax(observed):
    State, _, activity, _ = observed
    child = OSError("Owned child " + "x" * 5000 + " CHILD END")
    child.add_note("Owned child note")
    syntax = SyntaxError("Owned syntax error", ("owned-mod.py", 17, 3, "bad)\n"))
    error = ExceptionGroup("Owned save group", [child, syntax, child])
    error.__cause__ = ValueError("Owned original cause")
    error.add_note("Owned group note")
    state = State()
    state.fail_save = error
    with pytest.raises(ExceptionGroup) as raised:
        state.save_season()
    assert raised.value is error and activity.writer.wait_idle(2)
    text = next(record["traceback"] for record in activity.records if record["level"] == "ERROR")
    assert "Owned original cause" in text and "direct cause" in text
    assert text.count("CHILD END") == 2 and "Owned child note" in text
    assert "Owned group note" in text and "owned-mod.py" in text and "line 17" in text
    assert "SyntaxError: Owned syntax error" in text and "bad)" in text
