# SPDX-License-Identifier: MPL-2.0
"""Check read-only startup; copied beside entry.py and run with bundled Python."""

import importlib.util
import os
from pathlib import Path
import runpy
import sys

assert importlib.util.find_spec("pip") is None


def unexpected(*args, **kwargs):
    raise AssertionError("startup attempted network or subprocess setup")


def audit(event, args):
    # Python's platform.machine() queries Windows using ver when WMI is unavailable.
    # Permit only that read-only query, not package installation or another command.
    if event == "subprocess.Popen" and sys.platform == "win32":
        executable, command, _, _ = args
        expected = Path(os.environ["SystemRoot"]) / "System32/cmd.exe"
        if (
            Path(executable).resolve() == expected.resolve()
            and command == f'{executable} /c "ver"'
        ):
            return
    if event in ("socket.connect", "subprocess.Popen"):
        unexpected()


sys.addaudithook(audit)
sys.argv = ["entry.py", "--check-only"]
runpy.run_path(str(Path(__file__).with_name("entry.py")), run_name="__main__")
