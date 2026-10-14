# SPDX-License-Identifier: MPL-2.0
"""An owned process tree for reduced-binding acceptance; no game files are used."""

from pathlib import Path
import os
import subprocess
import sys

if len(sys.argv) == 1:
    command = [sys.executable, "-I", "-S", "-B", str(Path(__file__)), "child"]
    if os.name == "posix":
        # Exercise both POSIX lifecycle events explicitly. Attaching during Python's
        # subprocess fork implementation stalls with stock and reduced Frida on WSL.
        child = os.fork()
        if child == 0:
            os.execv(sys.executable, command)
        _, status = os.waitpid(child, 0)
        assert status == 0, status
    else:
        subprocess.run(command, check=True, timeout=8)
    print("owned parent output", flush=True)
else:
    print("owned child output", flush=True)
