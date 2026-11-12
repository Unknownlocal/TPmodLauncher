# SPDX-License-Identifier: MPL-2.0
"""Retain completed Proton failures in the launcher's own console."""

from contextlib import nullcontext
from copy import deepcopy
import importlib.util
from pathlib import Path, PurePosixPath


class LinuxFailureChannel:
    """Keep log ownership through console dismissal without native desktop tools.

    Proton still runs Windows Python. FailureSession selects that runtime's
    console APIs while displaying the Linux host path and disabling desktop UI.
    The existing explicit owner channel cancels the notice without polling.
    """

    def __init__(self, host_log_path, *, host_lifetime=None, session_factory=None):
        if not PurePosixPath(host_log_path).is_absolute():
            raise ValueError("The Linux log path must be absolute")
        self.host_log_path = host_log_path
        self.host_lifetime = host_lifetime
        self.session_factory = session_factory

    def __call__(self, outcome, *, owned_log, wait, ownership=None):
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
        session = factory(value, owned_log=owned_log, ownership=ownership, host_platform="linux")
        with self.host_lifetime.watch(session.dismiss) if self.host_lifetime is not None else nullcontext():
            session.run(wait=wait)
