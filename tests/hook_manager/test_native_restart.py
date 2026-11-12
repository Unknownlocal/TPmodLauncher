# SPDX-License-Identifier: MPL-2.0
"""Exercise restart-only observation without changing native results or waiting on I/O."""

import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Optional

import pytest

from tests.hook_manager.fixtures.native_events_support import native_events, records


@pytest.fixture
def restart_events(native_settings, monkeypatch, request):
    outcome = SimpleNamespace(error=SystemExit(), result=None, calls=[])
    def relaunch_application(*, series_mode: Optional[str] = None) -> None:
        outcome.calls.append(series_mode)
        if outcome.error is not None:
            raise outcome.error
        return outcome.result
    options = sys.modules["ui.screens.options_screen"]
    monkeypatch.setattr(options, "relaunch_application", relaunch_application, raising=False)
    pack = ModuleType("ui.screens.season_pack_screen")
    pack.relaunch_application = relaunch_application
    monkeypatch.setitem(sys.modules, pack.__name__, pack)
    host = request.getfixturevalue("native_events")[0]
    return host, options, pack, outcome, relaunch_application


def test_both_screen_aliases_mark_restarts_but_direct_quit_is_untouched(restart_events):
    host, settings, pack, outcome, original = restart_events
    coverage = [row for row in records(host) if row.get("event") == "restart-observation"]
    assert set(coverage[-1]["status"]["groups"].values()) == {"active"}
    for function, series in ((settings.relaunch_application, None), (pack.relaunch_application, "oval")):
        with pytest.raises(SystemExit) as stopped:
            function(series_mode=series)
        assert stopped.value is outcome.error
    changes = [row["status"] for row in records(host) if row.get("event") == "restart"]
    assert [(row["attempt"], row["state"], row["source"], row["series"]) for row in changes] == [
        (1, "requested", "settings", None), (1, "committed", "settings", None),
        (2, "requested", "mod-pack", "oval"), (2, "committed", "mod-pack", "oval")]
    # No global SystemExit/Popen/quit hook: the same exit outside those aliases
    # generates no restart intent, and cleanup restores the screen references.
    with pytest.raises(SystemExit):
        original()
    assert not any(row.get("event") == "restart" for row in records(host))
    host.events.restart.stop()
    assert settings.relaunch_application is original and pack.relaunch_application is original


def test_failed_or_returning_restart_clears_attempt_and_preserves_native_outcome(restart_events):
    host, settings, _, outcome, _ = restart_events
    outcome.error = OSError("Owned replacement could not start")
    with pytest.raises(OSError) as failed:
        settings.relaunch_application()
    assert failed.value is outcome.error
    outcome.error, outcome.result = None, object()
    assert settings.relaunch_application() is outcome.result
    changes = [row["status"] for row in records(host) if row.get("event") == "restart"]
    assert [(row["attempt"], row["state"]) for row in changes] == [
        (1, "requested"), (1, "cancelled"), (2, "requested"), (2, "cancelled")]


def test_stalled_journal_does_not_hold_restart_and_pressure_keeps_markers(restart_events):
    host, settings, _, outcome, _ = restart_events
    records(host)
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    written, failures = [], []
    def append_records(rows):
        entered.set()
        assert release.wait(5)
        written.extend(rows)
    host.activity.reporting.journal = SimpleNamespace(append_records=append_records)
    host.activity.submit_log({"level": "INFO", "detail": "Owned blocked append"})
    worker = None
    try:
        assert entered.wait(2)
        def restart():
            try:
                settings.relaunch_application()
            except SystemExit as error:
                failures.append(error)
            finally:
                returned.set()
        worker = threading.Thread(target=restart)
        worker.start()
        assert returned.wait(2) and failures == [outcome.error]
        for _ in range(1000):
            host.activity.submit_log({"level": "INFO", "detail": "routine backlog"})
        assert not written
    finally:
        release.set()
        if worker is not None:
            worker.join(2)
        assert host.activity.reporting.writer.wait_idle(2)
    assert [row["status"]["state"] for row in written if row.get("event") == "restart"] == ["requested", "committed"]
    assert any(row.get("dropped_records", 0) > 0 for row in written)


def test_reporting_failure_cannot_replace_native_exit(restart_events, monkeypatch):
    host, settings, _, outcome, _ = restart_events
    def unavailable(*args):
        raise RuntimeError("Owned reporting unavailable")
    monkeypatch.setattr(host.events, "publish", unavailable)
    with pytest.raises(SystemExit) as stopped:
        settings.relaunch_application()
    assert stopped.value is outcome.error and outcome.calls == [None]


def test_final_checkpoint_marks_replaced_observer_unavailable(restart_events, monkeypatch):
    host, settings, _, _, original = restart_events
    records(host)
    monkeypatch.setattr(settings, "relaunch_application", original)
    host.events.restart.finish_on_exit()
    final = [row["status"] for row in records(host) if row.get("event") == "restart-observation"]
    assert final[-1]["final"] is True and final[-1]["groups"]["restart.settings"] == "failed"
