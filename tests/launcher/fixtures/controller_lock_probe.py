# SPDX-License-Identifier: MPL-2.0
"""Probe a controller lock from another process; the test supplies its import path."""

from launcher.process_lifecycle import controller_lock, LaunchError
from pathlib import Path
import sys

try:
    with controller_lock(Path(sys.argv[1])):
        pass
except LaunchError:
    raise SystemExit(7)
