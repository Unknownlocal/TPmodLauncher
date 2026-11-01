# SPDX-License-Identifier: MPL-2.0
"""Exercise owned socket delivery, durable recovery and reporting failure handling."""

import os
import socket
import threading
from types import SimpleNamespace

import pytest

from launcher.diagnostics import reporter
from launcher.diagnostics.activity_journal import ActivityJournal
from launcher.diagnostics.exception_snapshot import ExceptionSnapshot
from launcher.diagnostics.journal_writer import JournalWriter
from launcher.diagnostics.reporting_receiver import ReportingReceiver
from launcher.diagnostics.reporting_sender import ReportingSender
from launcher.diagnostics.reporting_wire import reporting_write
from launcher.game_bridge import Bridge, BridgeError
from launcher.process_lifecycle import Controller, LaunchError
from tests.controller_device import rig


@pytest.fixture
def delivery(rig, tmp_path, monkeypatch):
    device, _, _, _ = rig
    path = tmp_path / "launcher.log"
    with reporter(path) as logger:
        controller = Controller(device, [device.game], logger)
        controller.start()
        session = controller.sessions[10]
        session.create_script = lambda *args, **kwargs: SimpleNamespace(on=lambda *args: None)
        enqueue = lambda message: controller.events.put(("bridge", (10, session, message)))
        receiver = ReportingReceiver(os.getpid(), "owned-session", lambda event, detail: enqueue(
            {"type": "send", "payload": {"event": event, "detail": detail}}))
        journal = ActivityJournal(tmp_path / "owned.jsonl")
        journal.create()
        bridge = Bridge(None, session, "", enqueue, journal, receiver)
        controller.bridges[10] = bridge
        reads, read = [], journal.read_available
        def read_available():
            reads.append(threading.get_ident())
            return read()
        monkeypatch.setattr(journal, "read_available", read_available)
        receiver.start()
        try:
            yield controller, bridge, receiver, path, reads
        finally:
            controller.close()


def writer_for(receiver):
    sender = ReportingSender(receiver.endpoint)
    return JournalWriter(sender.enqueue_log_record, sender)


def error_record(detail):
    try:
        raise OSError(detail)
    except OSError as error:
        error.add_note("Original save diagnostic note")
        return ({"level": "ERROR", "component": "activity", "detail": "NATIVE OPERATION FAILED",
                 "activity": {"kind": "saving", "generation": 1, "sequence": 1}}, ExceptionSnapshot(error))


def test_notifications_log_errors_without_idle_reads_or_game_pulses(delivery):
    controller, bridge, receiver, path, reads = delivery
    for _ in range(3):
        controller.poll()
    assert reads == []
    writer = writer_for(receiver)
    record, failure = error_record("Owned save failed FULL END")
    writer.enqueue_log_record(record, bridge.journal, failure)
    assert writer.finish(2) and receiver.finished.wait(2)
    controller.poll()
    text = path.read_text()
    assert "Owned save failed FULL END" in text and "Original save diagnostic note" in text
    assert '"kind":"saving"' in text.replace(" ", "")
    assert reads == [threading.get_ident()]
    assert not controller.stopping and receiver.confirmed


@pytest.mark.parametrize("replacement", ["game", "helper"])
def test_exec_drains_old_errors_without_servicing_replaced_image(delivery, replacement, tmp_path):
    controller, bridge, receiver, path, _ = delivery
    previous = controller.sessions[10]
    controller.pack_mods[10] = SimpleNamespace(close=lambda: bridge.set_menu([]))
    bridge.journal.append_record({"event": "native-service"})
    bridge.journal.append_record({"event": "diagnostic", "level": "ERROR",
                                  "detail": "Old native error", "traceback": "x" * 5000 + " FULL EXEC END"})
    controller.device.processes[10] = controller.game if replacement == "game" else str(tmp_path / "helper.exe")
    controller.events.put(("child", SimpleNamespace(pid=10, parent_pid=10, origin="exec")))
    controller.poll()
    assert bridge.exited and 10 not in controller.bridges and 10 not in controller.pack_mods
    assert previous.is_detached and receiver.finished.is_set() and not controller.cleanup_failed
    assert (10 in controller.sessions) == (replacement == "game")
    if replacement == "game":
        assert controller.sessions[10] is not previous
    assert "x" * 5000 + " FULL EXEC END" in path.read_text()


def test_reporting_eof_before_exit_callback_cannot_describe_reused_pid(delivery, tmp_path, monkeypatch):
    controller, bridge, _, path, _ = delivery
    session = controller.sessions[10]
    bridge.journal.append_record({"event": "native-service"})
    bridge.journal.append_record({"event": "diagnostic", "level": "ERROR", "detail": "Final owned error"})
    controller.events.put(("bridge", (10, session, {"type": "send",
        "payload": {"event": "reporting-closed", "detail": "EOF"}})))
    controller.device.exit(10)
    controller.device.processes[10] = str(tmp_path / "foreign.exe")
    def no_pid_lookup(**kwargs):
        raise AssertionError("Exit recovery inspected a reused PID")
    monkeypatch.setattr(controller.device, "enumerate_processes", no_pid_lookup)
    controller.poll()
    assert bridge.exited and not controller.sessions and not controller.cleanup_failed
    assert bridge.journal.retained and "Final owned error" in path.read_text()


def test_control_completion_bypasses_a_stalled_journal(delivery, monkeypatch):
    _, bridge, receiver, _, _ = delivery
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    append = bridge.journal.append_records
    def stalled(records):
        entered.set()
        assert release.wait(3)
        append(records)
    monkeypatch.setattr(bridge.journal, "append_records", stalled)
    received = []
    def control_ready(request, dependency):
        received.append((request, dependency))
        completed.set()
    receiver.control_ready = control_ready
    writer = writer_for(receiver)
    try:
        writer.enqueue_log_record({"level": "INFO", "detail": "delayed storage"}, bridge.journal)
        assert entered.wait(2)
        writer.notifications.enqueue_control_ready("a" * 32, "operations")
        assert completed.wait(2) and received == [("a" * 32, "operations")]
        assert not writer.wait_idle(.01)
    finally:
        release.set()
        assert writer.finish(2) and receiver.finished.wait(2)


def test_failed_journal_delivers_full_multiframe_error_over_socket(delivery, tmp_path):
    controller, bridge, receiver, path, _ = delivery
    writer = writer_for(receiver)
    record, failure = error_record("Owned large error " + "x" * 180_000 + " FULL SOCKET END")
    writer.enqueue_log_record(record, ActivityJournal(tmp_path / "missing" / "journal"), failure)
    assert writer.finish(2) and receiver.finished.wait(2)
    controller.poll()
    text = path.read_text()
    assert "Native activity journal failed" in text
    assert "x" * 180_000 + " FULL SOCKET END" in text and "Original save diagnostic note" in text
    assert not controller.stopping and receiver.confirmed
    assert bridge.journal.path.exists()  # A caught save error is not a fatal exit.


def test_torn_append_cannot_hide_socket_only_fatal_details(delivery, monkeypatch):
    controller, bridge, receiver, path, _ = delivery
    append = bridge.journal.append_records
    def interrupted(records):
        if any(record["level"] == "ERROR" for record in records):
            with bridge.journal.path.open("ab") as stream:
                stream.write(b'{"partial":')
            raise OSError("Owned partial-write failure")
        append(records)
    monkeypatch.setattr(bridge.journal, "append_records", interrupted)
    writer = writer_for(receiver)
    first, first_failure = error_record("First durable native failure FULL END")
    first.update(level="CRITICAL", fatal=True, reason="First durable native failure FULL END")
    writer.enqueue_log_record(first, bridge.journal, first_failure)
    record, failure = error_record("Original socket-only native failure FULL END")
    record.update(fatal=True, reason="Original socket-only native failure FULL END")
    writer.enqueue_log_record(record, bridge.journal, failure)
    writer.enqueue_log_record({"level": "INFO", "detail": "later complete append"}, bridge.journal)
    assert writer.finish(2) and receiver.finished.wait(2)
    with pytest.raises(LaunchError, match="First durable native failure FULL END"):
        controller.poll()
    text = path.read_text()
    assert "Original save diagnostic note" in text and "Native activity journal failed" in text
    assert "Original socket-only native failure FULL END" in text and "First durable native failure FULL END" in text
    assert "NATIVE JOURNAL READ FAILED" in text and "later complete append" in text
    assert bridge.journal.read_failed and bridge.journal.path.exists()


def test_blocked_notification_sender_does_not_delay_journal_appends(delivery, monkeypatch):
    from launcher.diagnostics import reporting_sender

    _, bridge, receiver, _, reads = delivery
    entered, release = threading.Event(), threading.Event()
    write = reporting_sender.reporting_write
    def blocked(connection, sequence, message):
        if message.get("kind") == "journal-ready":
            entered.set()
            assert release.wait(3)
        write(connection, sequence, message)
    monkeypatch.setattr(reporting_sender, "reporting_write", blocked)
    writer = writer_for(receiver)
    try:
        writer.enqueue_log_record({"level": "INFO", "detail": "first durable record"}, bridge.journal)
        assert entered.wait(2)
        writer.enqueue_log_record({"level": "ERROR", "detail": "second durable record"}, bridge.journal)
        assert writer.wait_idle(2)
        assert "second durable record" in bridge.journal.path.read_text()
        assert reads == []
        writer.close()
        assert not writer.finished.is_set()
    finally:
        release.set()
        assert writer.finish(2) and receiver.finished.wait(2)


def test_foreign_peer_cannot_latch_receiver_and_cancel_wakes_partial_read(delivery):
    _, bridge, receiver, _, _ = delivery
    endpoint = receiver.endpoint
    with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=2) as foreign:
        reporting_write(foreign, 0, {"version": 1, "session": endpoint["session"],
                                     "pid": os.getpid(), "token": "\u00e9" * 64})
    with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=2) as owned:
        reporting_write(owned, 0, {"version": 1, "session": endpoint["session"],
                                   "pid": os.getpid(), "token": endpoint["token"]})
        assert receiver.connected.wait(2)
        owned.sendall(b"\0")  # A started but incomplete frame must be cancellable.
        receiver.close()
        assert receiver.finished.is_set() and not receiver.confirmed
    bridge.finish_reporting()
    assert bridge.journal.retained


def test_reporting_fault_recovers_journal_and_keeps_evidence(delivery):
    controller, bridge, receiver, path, _ = delivery
    record, _ = error_record("unused")
    record["detail"] = "Last durable save error before channel failure"
    bridge.journal.append_record(record)
    endpoint = receiver.endpoint
    with socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=2) as owned:
        reporting_write(owned, 0, {"version": 1, "session": endpoint["session"],
                                   "pid": os.getpid(), "token": endpoint["token"]})
        reporting_write(owned, 2, {"kind": "journal-ready"})  # Skip sequence one.
        assert receiver.finished.wait(2)
    with pytest.raises(LaunchError, match="reporting sequence"):
        controller.poll()
    assert "Last durable save error before channel failure" in path.read_text()
    assert bridge.journal.retained


def test_disappeared_live_script_retains_journal_before_reporting_connects(delivery):
    _, bridge, receiver, _, _ = delivery
    bridge.loaded = True
    bridge.script.is_destroyed = True
    bridge.journal.append_record({"level": "ERROR", "detail": "Unconfirmed native final record"})
    assert not receiver.connected.is_set()
    with pytest.raises(BridgeError, match="Bridge script disappeared"):
        bridge.close()
    bridge.journal.remove()
    assert bridge.journal.path.exists() and bridge.journal.retained


def test_final_exit_recovers_a_record_without_a_notification(delivery, monkeypatch):
    controller, bridge, receiver, path, reads = delivery
    monkeypatch.setattr(ReportingSender, "enqueue_journal_notice", lambda self: None)
    writer = writer_for(receiver)
    record, failure = error_record("Durable failure with lost notice FULL END")
    writer.enqueue_log_record(record, bridge.journal, failure)
    assert writer.finish(2) and receiver.finished.wait(2)
    controller.poll()
    assert reads == [] and "FULL END" not in path.read_text()
    session = controller.sessions[10]
    session.is_detached = True
    controller.events.put(("detached", (10, session, "process-terminated")))
    controller.poll()
    assert "Durable failure with lost notice FULL END" in path.read_text()
    assert not bridge.journal.path.exists()


def test_pending_import_service_cannot_hide_fatal_in_same_journal_batch(delivery):
    controller, bridge, receiver, path, _ = delivery
    writer = writer_for(receiver)
    writer.enqueue_log_record({"event": "native-service"}, bridge.journal)
    record, failure = error_record("Owned fatal before import service FULL END")
    record.update(fatal=True, reason="Owned fatal before import service FULL END")
    writer.enqueue_log_record(record, bridge.journal, failure)
    assert writer.finish(2) and receiver.finished.wait(2)
    with pytest.raises(LaunchError, match="Owned fatal before import service FULL END"):
        controller.poll()
    assert "Original save diagnostic note" in path.read_text()


def test_late_fallback_status_cannot_replace_newer_settings(delivery, tmp_path):
    controller, bridge, receiver, _, _ = delivery
    accepted = []
    controller.pack_mods[10] = SimpleNamespace(settings_status=accepted.append, poll=lambda: None, close=lambda: None)
    writer = writer_for(receiver)
    current = {"save": None, "generation": 2}
    writer.enqueue_log_record({"event": "settings", "event_sequence": 2, "status": current}, bridge.journal)
    writer.enqueue_log_record({"event": "settings", "event_sequence": 1, "status": {"save": {"id": 1}, "generation": 1}},
                              ActivityJournal(tmp_path / "missing" / "journal"))
    assert writer.finish(2) and receiver.finished.wait(2)
    controller.poll()
    assert accepted == [current]


def test_semantic_journal_faults_preserve_later_complete_fatal_and_raw_evidence(delivery):
    controller, bridge, receiver, path, _ = delivery
    writer = writer_for(receiver)
    writer.enqueue_log_record({"event": "unsupported-native-change", "detail": "UNKNOWN DATA END"}, bridge.journal)
    writer.enqueue_log_record({"event": "settings", "event_sequence": "invalid", "status": {"detail": "SEQUENCE DATA END"}}, bridge.journal)
    record, failure = error_record("Original native fatal after invalid change FULL END")
    record.update(fatal=True, reason="Original native fatal after invalid change FULL END")
    writer.enqueue_log_record(record, bridge.journal, failure)
    assert writer.finish(2) and receiver.finished.wait(2)
    with pytest.raises(LaunchError, match="Original native fatal after invalid change FULL END"):
        controller.poll()
    text = path.read_text()
    assert "Original save diagnostic note" in text
    assert "UNKNOWN DATA END" in text and "SEQUENCE DATA END" in text
    controller.close()
    assert bridge.journal.path.exists() and bridge.journal.retained
