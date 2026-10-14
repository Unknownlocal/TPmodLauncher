# SPDX-License-Identifier: MPL-2.0
"""Spawn owned helper and replacement processes and record their launch settings."""

import json, os, subprocess, sys, time
from pathlib import Path

mode, output = sys.argv[1], Path(sys.argv[2])


def spawn(command, *, wait=False):
    """Use explicit POSIX fork/exec so both gated lifecycle events can be tested."""
    if os.name == "posix":
        reader, writer = os.pipe()  # Python marks these descriptors close-on-exec.
        child = os.fork()
        if child == 0:
            os.close(reader)
            # A GIL boundary before exec must not bootstrap a manager into inherited state.
            time.sleep(0.15)
            os.execv(command[0], command)
        os.close(writer)
        # Match Popen's handoff: return only after exec closes the child's pipe end.
        with os.fdopen(reader, "rb") as handoff:
            assert handoff.read() == b""
        if wait:
            _, status = os.waitpid(child, 0)
            assert status == 0, status
    elif wait:
        subprocess.run(command, check=True, timeout=20)
    else:
        subprocess.Popen(command)


if mode == "parent":
    spawn([sys.argv[3], "-B", __file__, "helper", str(output)], wait=True)
    spawn([sys.executable, "-B", __file__, "replacement", str(output), *sys.argv[4:]])
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
