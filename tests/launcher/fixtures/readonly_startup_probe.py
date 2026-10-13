# SPDX-License-Identifier: MPL-2.0
"""Check read-only startup; copied beside entry.py and run with bundled Python."""

import importlib.util
from pathlib import Path
import runpy
import sys

assert importlib.util.find_spec("pip") is None


def unexpected(*args, **kwargs):
    raise AssertionError("startup attempted network or subprocess setup")


def audit(event, args):
    if event in ("socket.connect", "subprocess.Popen"):
        unexpected()


sys.addaudithook(audit)
sys.argv = ["entry.py", "--check-only"]
runpy.run_path(str(Path(__file__).with_name("entry.py")), run_name="__main__")
