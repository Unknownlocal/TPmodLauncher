# SPDX-License-Identifier: MPL-2.0
"""Collect records from the real background writer without a reporting socket."""

from collections import deque

from hook_manager.native_activity import NativeActivity
from launcher.diagnostics.journal_writer import JournalWriter


class CollectedActivity(NativeActivity):
    """Keep an in-memory output only for tests that inspect native observation."""

    def __init__(self, readiness, journal=None):
        self.records = deque()
        super().__init__(readiness, journal, writer=JournalWriter(self.records.append))
