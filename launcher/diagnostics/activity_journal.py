# SPDX-License-Identifier: MPL-2.0
"""Transfer native diagnostics without waiting for the game's main thread."""

import json
import os
from pathlib import Path
import threading


class ActivityJournal:
    """Own one launch/process file; its reader consumes complete appended records."""

    def __init__(self, path):
        self.path, self.offset = Path(path), 0
        self.owned = False
        self.read_failed = False
        self.retained = False
        self._lock = threading.RLock()

    def create(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("xb"):
            pass
        self.owned = True

    def append_record(self, record):
        """Append a complete line and synchronise failures before returning."""
        data = (json.dumps(record, ensure_ascii=True) + "\n").encode("utf-8")
        with self._lock:
            # Never recreate a journal after its controller has removed it.
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0))
            with os.fdopen(fd, "ab") as stream:
                stream.write(data)
                stream.flush()
                if record.get("level") in ("ERROR", "CRITICAL"):
                    os.fsync(stream.fileno())

    def read_available(self):
        records = []
        if not self.path.exists():
            return records
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break  # The writer may still be finishing this record.
                try:
                    records.append(json.loads(line))
                except (ValueError, UnicodeError) as error:
                    self.read_failed = True
                    # A failed write can leave a fragment before a later record.
                    # Retain the evidence and include all available text in the
                    # launcher's complete error rather than deleting its cause.
                    stream.seek(0)
                    raw = stream.read().decode("utf-8", errors="replace")
                    raise ValueError("Cannot decode native activity journal " + str(self.path) + ":\n" + raw) from error
                self.offset = stream.tell()
        return records

    def remove(self):
        """Dispose after the final drain, retaining unread or damaged evidence."""
        if self.owned and not self.read_failed and not self.retained:
            with self.path.open("rb") as stream:
                stream.seek(self.offset)
                if stream.read():
                    # Disposal follows native shutdown/exit. An unfinished last
                    # line can no longer become complete; preserve it as evidence.
                    self.read_failed = True
                    stream.seek(0)
                    raw = stream.read().decode("utf-8", errors="replace")
                    raise ValueError("Unread native activity journal " + str(self.path) + ":\n" + raw)
            self.path.unlink(missing_ok=True)
            self.owned = False
