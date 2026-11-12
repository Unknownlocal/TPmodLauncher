# SPDX-License-Identifier: MPL-2.0
"""Separate native work notifications from durable diagnostic delivery."""

if __package__:
    from .exception_snapshot import ExceptionSnapshot
    from launcher.diagnostics.journal_writer import JournalWriter
    from launcher.diagnostics.reporting_sender import ReportingSender


class NativeReporting:
    """Own one reporting lifetime; game callbacks enqueue memory only.

    State and errors share journal ordering. Work notifications go straight to
    the sender, so storage cannot hold up the next native service request.
    """

    def __init__(self, journal=None, endpoint=None, *, writer=None):
        if writer is None:
            if endpoint is None:
                raise ValueError("Native reporting needs an endpoint or an explicit writer")
            sender = ReportingSender(endpoint)
            writer = JournalWriter(sender.enqueue_log_record, sender)
        self.journal, self.writer = journal, writer

    def enqueue_event(self, record):
        return self.writer.enqueue_log_record(record, self.journal)

    def submit_log(self, record, *, error=None):
        """Capture a complete error without formatting it on the calling thread."""
        if error is not None and "exception_snapshot" not in record:
            record = {**record, "exception_snapshot": ExceptionSnapshot(error).to_data()}
        return self.enqueue_event(record)

    def control_ready(self, request, dependency):
        sender = self.writer.notifications
        if request is not None and sender is not None:
            sender.enqueue_control_ready(request, dependency)

    def service_ready(self):
        sender = self.writer.notifications
        if sender is not None:
            sender.enqueue_service_ready()
        else:
            # Explicit collectors used by observer tests have no socket worker.
            self.writer.enqueue_fallback({"event": "native-service"})
