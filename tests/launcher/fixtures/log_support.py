# SPDX-License-Identifier: MPL-2.0
"""Await owned diagnostic output before asserting its file and console contents."""

import logging

from launcher.diagnostics import sync_log


def read_log(path):
    logger = logging.getLogger("modLauncher")
    output = getattr(logger, "launch_output", None)
    if output is not None:
        assert sync_log(logger), "Owned log did not synchronise"
        assert output.console.checkpoint(1), "Owned console did not drain"
    return path.read_text(encoding="utf-8")
