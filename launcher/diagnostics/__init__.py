# SPDX-License-Identifier: MPL-2.0
"""Launcher logging, fatal outcomes and owned-game shutdown support."""

from .core import (
    bind_reporter,
    build_launch_outcome,
    exception_from_record,
    handle_error,
    log_ownership,
    reporter,
    sync_log,
    write_log,
)
