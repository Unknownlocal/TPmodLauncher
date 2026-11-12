# SPDX-License-Identifier: MPL-2.0
"""Keep a launch's lock alive while its file worker can still write."""

import os
import threading


class LogOwnership:
    """Release the OS lock only after both the caller and its writers finish."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = path.open("a+b", buffering=0)
        self._lock, self._references = threading.Lock(), 1
        try:
            if self._stream.seek(0, os.SEEK_END) == 0:
                self._stream.write(b"\0")
            self._stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self._stream.close()
            raise

    def retain(self):
        with self._lock:
            if not self._references:
                raise RuntimeError("Launcher log ownership has ended")
            self._references += 1

    def release(self):
        stream = None
        with self._lock:
            self._references -= 1
            if not self._references:
                stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()
