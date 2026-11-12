# SPDX-License-Identifier: MPL-2.0
"""File operations owned exclusively by the launcher's file-output worker."""

import os


class LogFileHandler:
    """Keep blocking file I/O outside logging's global shutdown/handler locks."""

    def __init__(self, path, *, mode="w"):
        self.path, self.mode = path, mode
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self._open()

    def _open(self):
        return self.path.open(self.mode, encoding="utf-8", errors="backslashreplace")

    def write(self, text):
        self.stream.write(text)
        self.stream.flush()

    def sync(self):
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()
