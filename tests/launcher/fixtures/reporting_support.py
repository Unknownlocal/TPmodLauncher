# SPDX-License-Identifier: MPL-2.0
"""Shared real reporting connection and error records for diagnostics tests."""

import os
import threading
from types import SimpleNamespace

import pytest

from launcher.diagnostics import reporter
from launcher.diagnostics.activity_journal import ActivityJournal
from launcher.diagnostics.exception_snapshot import ExceptionSnapshot
from launcher.diagnostics.journal_writer import JournalWriter
from launcher.diagnostics.reporting_receiver import ReportingReceiver
from launcher.diagnostics.reporting_sender import ReportingSender
from launcher.process_lifecycle import Controller
from launcher.game_bridge import Bridge


@pytest.fixture
def delivery(rig, tmp_path, monkeypatch):
    device, _, _, _ = rig
    path = tmp_path / "launcher.log"
    with reporter(path) as logger:
        controller = Controller(device, [device.game], logger)
        controller.start()
        owner = controller.processes[10]
        session = owner.session
        session.create_script = lambda *args, **kwargs: SimpleNamespace(on=lambda *args: None)
        enqueue = lambda message: controller._queue_bridge(owner, message)
        receiver = ReportingReceiver(os.getpid(), "owned-session", lambda event, detail: enqueue(
            {"type": "send", "payload": {"event": event, "detail": detail}}))
        journal = ActivityJournal(tmp_path / "owned.jsonl")
        journal.create()
        bridge = Bridge(None, session, "", enqueue, journal, receiver)
        controller.processes[10].bridge = bridge
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


