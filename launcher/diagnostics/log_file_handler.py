# SPDX-License-Identifier: MPL-2.0
"""Retain file-output failures even when Python logging suppresses them."""

import logging
import sys


class LogFileHandler(logging.FileHandler):
    """Retain write failures through a callback instead of logging recursively."""

    def __init__(self, path, store_error):
        self.store_error = store_error
        super().__init__(path, mode="w", encoding="utf-8", errors="backslashreplace")

    def handleError(self, record):
        # StreamHandler catches write/flush errors here even when raiseExceptions
        # is false. The console handler has already received the original record.
        self.store_error(sys.exception())
