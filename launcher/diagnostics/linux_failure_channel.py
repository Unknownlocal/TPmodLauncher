# SPDX-License-Identifier: MPL-2.0
"""Hand a completed Proton failure to its native Linux supervisor."""

from copy import deepcopy
from contextlib import nullcontext
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import select
import socket
import sys
import time
import traceback


class LinuxFailureChannel:
    """Retain entry's log ownership until the host notice is dismissed or lost.

    This runs only after game cleanup. A stdout control line wakes the Bash
    supervisor; its notice connects over loopback, then EOF links their lifetimes.
    Neither side polls files, and no Linux Python installation is needed.
    """

    def __init__(self, root, session, host_log_path, *, startup_timeout=10, host_lifetime=None):
        if not re.fullmatch(r"linux-notice\.[A-Za-z0-9]{10}", session):
            raise ValueError("Invalid Linux notice session")
        if not PurePosixPath(host_log_path).is_absolute():
            raise ValueError("The Linux log path must be absolute")
        self.directory = Path(root) / ".state" / session
        self.session, self.host_log_path = session, host_log_path
        self.startup_timeout = startup_timeout
        self.host_lifetime = host_lifetime
        self._wake_reader = self._wake_writer = None

    def _cancel(self):
        if self._wake_writer is not None:
            try:
                self._wake_writer.send(b"x")
            except OSError:
                pass

    def _ready(self, stream, deadline=None):
        """Wait for actual data or owner loss at every protocol boundary."""
        if self.host_lifetime is not None and self.host_lifetime.lost.is_set():
            return False
        timeout = None if deadline is None else max(0, deadline - time.monotonic())
        ready, _, _ = select.select([stream, self._wake_reader], [], [], timeout)
        if self._wake_reader in ready:
            return False
        if not ready:
            raise TimeoutError("The native Linux failure console did not connect")
        return True

    def __call__(self, outcome, *, owned_log, wait):
        """Publish only this launch's outcome, with a bounded connection setup."""
        try:
            reader, writer = socket.socketpair()
            with reader, writer:
                self._wake_reader, self._wake_writer = reader, writer
                writer.setblocking(False)
                with self.host_lifetime.watch(self._cancel) if self.host_lifetime is not None else nullcontext():
                    self._present(outcome, owned_log=owned_log, wait=wait)
        except Exception:
            if self.host_lifetime is not None and self.host_lifetime.lost.is_set():
                return  # Owner loss dismisses the notice, preserving the failure code.
            detail = "Native Linux failure presentation failed:\n" + traceback.format_exc()
            print(detail, file=sys.stderr, flush=True)
            if owned_log and outcome["log_written"]:
                try:
                    with Path(outcome["log_path"]).open("a", encoding="utf-8") as stream:
                        stream.write("\nPRESENTATION ERROR " + detail)
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError:
                    print("Could not append presentation details:\n" + traceback.format_exc(), file=sys.stderr, flush=True)
            raise

    def _present(self, outcome, *, owned_log, wait):
        if self.host_lifetime is not None and self.host_lifetime.lost.is_set():
            return
        value = deepcopy(outcome)
        value.update(host_platform="linux", host_log_path=self.host_log_path,
                     log_written=bool(owned_log and outcome["log_written"]))
        failure = value["failure"]
        activity = (failure.get("activity") or {}).get("description", "during launcher operation")
        reason = str(failure["reason"]).splitlines()
        text = ("Mod Launcher could not continue\nAn error occurred " + activity + ".\n"
                + (reason[0] if reason else "Unknown failure")[:512] + "\n"
                + ("Full details: " if value["log_written"] else "No completed log for this launch: ")
                + self.host_log_path + "\n")
        if failure.get("details"):
            text += "\n" + failure["details"] + "\n"
        print(text, file=sys.stderr, flush=True)
        if not wait:
            return
        # The native wrapper creates a private, unique directory. Do not create
        # missing sessions or publish into a link supplied by another launch.
        if not self.directory.is_dir() or self.directory.is_symlink():
            raise OSError("The Linux notice directory is unavailable")
        for name, content in (("outcome.json", json.dumps(value, ensure_ascii=True)), ("notice.txt", text)):
            with (self.directory / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        if value["log_written"]:
            (self.directory / "log-complete").touch(exist_ok=False)
        token = secrets.token_hex(32)
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(4)
            if self.host_lifetime is not None and self.host_lifetime.lost.is_set():
                return
            deadline = time.monotonic() + self.startup_timeout
            print(f"MOD_LAUNCHER_NOTICE {self.session} {server.getsockname()[1]} {token}", flush=True)
            while True:
                if not self._ready(server, deadline):
                    return
                connection, _ = server.accept()
                with connection:
                    received = bytearray()
                    while len(received) < 65:
                        if not self._ready(connection, deadline):
                            return
                        part = connection.recv(65 - len(received))
                        if not part:
                            break
                        received.extend(part)
                        if b"\n" in part:
                            break
                    if not hmac.compare_digest(bytes(received), (token + "\n").encode("ascii")):
                        continue
                    connection.sendall(b"ready\n")
                    # There is deliberately no user-reading deadline. Native
                    # console/dialog dismissal closes this connection; losing
                    # the controller closes the host notice in the other direction.
                    if self._ready(connection):
                        connection.recv(1)
                    return
