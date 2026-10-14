# SPDX-License-Identifier: MPL-2.0
"""An owned process tree for reduced-binding acceptance; no game files are used."""

from pathlib import Path
import subprocess
import sys

if len(sys.argv) == 1:
    subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(Path(__file__)), "child"],
        check=True,
        timeout=8,
    )
    print("owned parent output", flush=True)
else:
    print("owned child output", flush=True)
