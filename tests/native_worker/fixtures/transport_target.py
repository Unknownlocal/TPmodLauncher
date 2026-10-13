# SPDX-License-Identifier: MPL-2.0
"""An owned cross-platform target that records release and produces ordinary stdout."""

from pathlib import Path
import sys
import time

Path(sys.argv[1]).write_text("released", encoding="utf-8")
print("ordinary target output", flush=True)
# Bound abandoned tests without needing the controller to kill the target.
time.sleep(2)
