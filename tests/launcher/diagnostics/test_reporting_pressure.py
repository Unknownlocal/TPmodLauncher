# SPDX-License-Identifier: MPL-2.0
"""Exercise queued overload and recovery through real journal/socket delivery."""

import threading

import pytest

from launcher.diagnostics import activity_journal, reporting_sender
from launcher.diagnostics.activity_journal import ActivityJournal
from launcher.diagnostics.journal_writer import JournalWriter
from launcher.process_lifecycle import LaunchError
from tests.controller_device import rig
from tests.launcher.diagnostics.test_reporting_delivery import delivery, error_record, writer_for


def test_stalled_writer_batches_routine_storm_and_preserves_first_fatal(delivery, monkeypatch):
    controller, bridge, receiver, path, reads = delivery
    entered, release = threading.Event(), threading.Event()
    batches, syncs = [], []
    append, sync = bridge.journal.append_records, activity_journal.os.fsync
    def stalled(records):
        if not batches:
            entered.set()
            assert release.wait(3)
        batches.append(len(records))
        append(records)
    def synchronise(fd):
        syncs.append(fd)
        sync(fd)
    monkeypatch.setattr(bridge.journal, "append_records", stalled)
    monkeypatch.setattr(activity_journal.os, "fsync", synchronise)
    writer = writer_for(receiver)
    try:
        writer.enqueue_log_record({"level": "INFO", "detail": "in flight"}, bridge.journal)
        assert entered.wait(2)
        for index in range(1000):
            writer.enqueue_log_record({"level": "INFO", "detail": f"routine {index}"}, bridge.journal)
            writer.enqueue_log_record({"event": "readiness", "status": {
                "watching": True, "career": index, "modules": [], "detail": ""}}, bridge.journal)
        for name in ("First fatal", "Second fatal"):
            record, failure = error_record(name + "x" * 7000 + " FULL END")
            record.update(fatal=True, reason=name)
            writer.enqueue_log_record(record, bridge.journal, failure)
        assert reads == [] and not writer.wait_idle(.01)
    finally:
        release.set()
        assert writer.finish(3) and receiver.finished.wait(3)
    # Inspect the durable stream before the actual controller consumes it.
    durable = ActivityJournal(bridge.journal.path).read_available()
    routine = [row for row in durable if row.get("detail", "").startswith("routine ")]
    assert len(routine) == 256 and routine[-1]["detail"] == "routine 999"
    assert sum(row.get("dropped_records", 0) for row in durable) == 744
    assert [row["status"]["career"] for row in durable if row.get("event") == "readiness"] == [999]
    assert len(batches) < 10 and max(batches) > 1 and len(syncs) == 2
    with pytest.raises(LaunchError, match="First fatal"):
        controller.poll()
    text = path.read_text()
    assert "First fatal" + "x" * 7000 + " FULL END" in text
    assert "Second fatal" + "x" * 7000 + " FULL END" in text
    assert "Original save diagnostic note" in text and "dropped 744" in text


def test_stalled_socket_bounds_fallback_and_coalesces_launcher_wakes(delivery, monkeypatch):
    controller, bridge, receiver, path, reads = delivery
    entered, release = threading.Event(), threading.Event()
    write = reporting_sender.reporting_write
    def stalled(connection, sequence, message):
        if message.get("kind") == "journal-ready" and not entered.is_set():
            entered.set()
            assert release.wait(3)
        write(connection, sequence, message)
    monkeypatch.setattr(reporting_sender, "reporting_write", stalled)
    sender = reporting_sender.ReportingSender(receiver.endpoint)
    try:
        sender.enqueue_journal_notice()
        assert entered.wait(2)
        for index in range(1000):
            sender.enqueue_log_record({"level": "INFO", "detail": f"fallback {index}"})
            sender.enqueue_journal_notice()
        record, failure = error_record("Original stalled socket error " + "x" * 7000 + " FULL END")
        record["traceback"] = "".join(failure.format())
        sender.enqueue_log_record(record)
    finally:
        release.set()
        sender.close()
        assert sender.finished.wait(3) and receiver.finished.wait(3)
    assert reads == []
    controller.poll()
    text = path.read_text()
    assert "x" * 7000 + " FULL END" in text and "fallback 999" in text
    assert "dropped 744" in text and "Original save diagnostic note" in text
    assert reads == [threading.get_ident()]  # One pending controller hint for the entire storm.


def test_formatted_snapshot_survives_source_lookup_failure(tmp_path, monkeypatch):
    import linecache
    from launcher.diagnostics.exception_snapshot import ExceptionSnapshot
    journal = ActivityJournal(tmp_path / "owned.jsonl")
    journal.create()
    try:
        raise ValueError("Original failure " + "x" * 7000 + " FULL END")
    except ValueError as error:
        error.add_note("Original diagnostic note")
        failure = ExceptionSnapshot(error)
    def unavailable(*args):
        raise OSError("Source cache unavailable")
    monkeypatch.setattr(linecache, "checkcache", unavailable)
    writer = JournalWriter(lambda record: pytest.fail(str(record)))
    writer.enqueue_log_record({"level": "ERROR", "detail": "captured failure"}, journal, failure)
    assert writer.finish(2)
    text = journal.read_available()[0]["traceback"]
    assert "Source cache unavailable" in text and "x" * 7000 + " FULL END" in text
    assert "Original diagnostic note" in text and "test_reporting_pressure.py" in text


def test_slow_launcher_bounds_received_info_but_keeps_saves_and_errors(delivery):
    _, bridge, receiver, _, _ = delivery
    acknowledged = threading.Event()
    receiver.control_ready = lambda request, dependency: acknowledged.set()
    sender = reporting_sender.ReportingSender(receiver.endpoint)
    try:
        # Keep the sender drained at each actual completion, while deliberately
        # leaving the launcher queue unserviced. Only the receiver overflows.
        for batch in range(8):
            acknowledged.clear()
            for index in range(batch * 128, (batch + 1) * 128):
                sender.enqueue_log_record({"level": "INFO", "detail": f"received {index}"})
            sender.enqueue_control_ready("a" * 32, "operations")
            assert acknowledged.wait(2)
        for request in (1, 2):
            sender.enqueue_log_record({"event": "settings", "status": {"save": {"id": request}}})
        record, failure = error_record("Complete error after receiver storm FULL END")
        record["traceback"] = "".join(failure.format())
        sender.enqueue_log_record(record)
    finally:
        sender.close()
        assert sender.finished.wait(3) and receiver.finished.wait(3)
    records = bridge.drain_fallback()
    assert sum(row.get("dropped_records", 0) for row in records) == 768
    routine = [row for row in records if row.get("detail", "").startswith("received ")]
    assert len(routine) == 256 and routine[-1]["detail"] == "received 1023"
    assert [row["status"]["save"]["id"] for row in records if row.get("event") == "settings"] == [1, 2]
    assert "FULL END" in records[-1]["traceback"] and "Original save diagnostic note" in records[-1]["traceback"]
