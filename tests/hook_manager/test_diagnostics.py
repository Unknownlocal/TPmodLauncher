# SPDX-License-Identifier: MPL-2.0
"""Keep generic native failures plain until their background/launcher boundary."""

import json
import linecache
import sys
import threading
from types import ModuleType

from hook_manager.manager import Manager
from hook_manager.diagnostics import error_text
from launcher.diagnostics import exception_from_record, handle_error, reporter
from launcher.diagnostics.activity_journal import ActivityJournal
from launcher.diagnostics.journal_writer import JournalWriter


def test_failed_import_observer_retains_plain_error_while_source_lookup_waits_on_worker(tmp_path, monkeypatch):
    module = ModuleType("owned_native_diagnostics")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    journal = ActivityJournal(tmp_path / "native.jsonl")
    journal.create()
    entered, release, readers = threading.Event(), threading.Event(), []
    game_thread = threading.get_ident()
    def blocked_source(filename=None):
        readers.append(threading.get_ident())
        assert threading.get_ident() != game_thread, "Native error capture read source files"
        entered.set()
        assert release.wait(2)
        raise OSError("Owned source lookup unavailable")
    def failed_import(imported):
        try:
            raise OSError("Original import cause " + "x" * 7000 + " FULL CAUSE END")
        except OSError as cause:
            error = RuntimeError("Owned import observer failed")
            error.add_note("Owned complete native note")
            raise error from cause
    writer = JournalWriter(lambda record: None)
    submitted = []
    def submit_log(record):
        submitted.append(record)
        writer.enqueue_log_record(record, journal)
        raise OSError("Owned diagnostic sink failed after accepting the record")
    manager = Manager(submit_log=submit_log)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(linecache, "checkcache", blocked_source)
            manager.observe(module.__name__, failed_import)
            retained = json.dumps(manager.error_record, sort_keys=True)
            reply = json.loads(json.dumps({"status": {"failure": manager.error_record}}))
            malformed = {"detail": "Owned malformed remote snapshot", "exception_snapshot": {}}
            reply["status"]["formatting"] = malformed
            assert manager.status()["errors"]
            assert submitted == [{**manager.error_record, "event": "diagnostic"}]
            assert entered.wait(1)
            assert manager.status()["state"] == "ready"
            # Wrapping a failed reply keeps its plain record even while source
            # lookup on the writer is stalled. It must not format on this thread.
            error = exception_from_record("Owned bridge refusal", reply)
            assert error.launcher_records == [reply["status"]["failure"], malformed]
            release.set()
            assert writer.finish(2)
        record, = journal.read_available()
        assert readers and set(readers) == {writer._thread.ident}
        assert "FULL CAUSE END" in record["traceback"] and "Owned complete native note" in record["traceback"]
        assert "Deferred error formatting failed" in record["traceback"]
        assert "exception_snapshot" in manager.error_record and "traceback" not in manager.error_record
        assert json.dumps(manager.error_record, sort_keys=True) == retained
        # A reply can reach the launcher before the journal's rendered record.
        assert "Owned malformed remote snapshot" in error_text(error)
        path = tmp_path / "launcher.log"
        with reporter(path) as logger:
            handle_error(logger, error)
        text = path.read_text()
        assert "FULL CAUSE END" in text and "Owned complete native note" in text
        assert "failed_import" in text and "direct cause" in text
    finally:
        release.set()
        writer.finish(2)
        manager.stop()
