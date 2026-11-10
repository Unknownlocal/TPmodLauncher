# SPDX-License-Identifier: MPL-2.0
"""Retain completed Proton failures in the launcher's own console."""

from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import traceback


class LinuxFailureChannel:
    """Keep log ownership through console dismissal without native desktop tools.

    Proton still runs Windows Python. FailureSession selects that runtime's
    console APIs while displaying the Linux host path and disabling desktop UI.
    The existing explicit owner channel cancels the notice without polling.
    """

    def __init__(self, root, session, host_log_path, *, host_lifetime=None, session_factory=None):
        if not re.fullmatch(r"linux-notice\.[A-Za-z0-9]{10}", session):
            raise ValueError("Invalid Linux notice session")
        if not PurePosixPath(host_log_path).is_absolute():
            raise ValueError("The Linux log path must be absolute")
        self.directory = Path(root) / ".state" / session
        self.host_log_path = host_log_path
        self.host_lifetime = host_lifetime
        self.session_factory = session_factory

    def __call__(self, outcome, *, owned_log, wait):
        """Print the host path and wait for a key, window close or owner loss."""
        if self.host_lifetime is not None and self.host_lifetime.lost.is_set():
            return
        value = deepcopy(outcome)
        value.update(host_platform="linux", host_log_path=self.host_log_path,
                     log_written=bool(owned_log and outcome["log_written"]))
        factory = self.session_factory
        if factory is None:
            # This source must also work before dependency verification/imports.
            source = Path(__file__).with_name("failure_session.py")
            spec = importlib.util.spec_from_file_location("_linux_failure_session", source)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            factory = module.FailureSession
        session = factory(value, owned_log=owned_log, host_platform="linux")
        if wait:
            try:
                if self.directory.is_symlink() or not self.directory.is_dir():
                    raise OSError("The Linux notice directory is unavailable")
                with (self.directory / "outcome.json").open("x", encoding="utf-8", newline="\n") as stream:
                    json.dump(value, stream, ensure_ascii=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                if value["log_written"]:
                    (self.directory / "log-complete").touch(exist_ok=False)
            except OSError:
                # Sidecar diagnostics cannot suppress the retained console or
                # replace the original failure and completed launcher.log.
                session._write_presentation_error("Could not retain session diagnostics:\n" + traceback.format_exc())
        with self.host_lifetime.watch(session.dismiss) if self.host_lifetime is not None else nullcontext():
            session.run(wait=wait)
