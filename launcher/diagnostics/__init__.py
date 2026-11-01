# SPDX-License-Identifier: MPL-2.0
"""Launcher logging, fatal outcomes, console notices and owned-game shutdown."""

from .core import (
    activity_scope,
    bind_reporter,
    build_launch_outcome,
    exception_from_record,
    handle_error,
    log_ownership,
    reporter,
    sync_log,
    write_log,
)
