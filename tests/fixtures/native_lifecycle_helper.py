# SPDX-License-Identifier: MPL-2.0
"""Spawn owned helper and replacement processes and record their launch settings."""

import json, os, subprocess, sys, time
from pathlib import Path

mode, output = sys.argv[1], Path(sys.argv[2])
if mode == "parent":
    subprocess.run(
        [sys.argv[3], "-B", __file__, "helper", str(output)], check=True, timeout=20
    )
    subprocess.Popen(
        [sys.executable, "-B", __file__, "replacement", str(output), *sys.argv[4:]]
    )
else:
    if mode == "replacement":
        time.sleep(0.4)
    (output / (mode + ".json")).write_text(
        json.dumps(
            {
                "argv": sys.argv[3:],
                "cwd": os.getcwd(),
                "sentinel": os.environ.get("TPM_TEST_SENTINEL"),
            }
        ),
        encoding="utf-8",
    )
