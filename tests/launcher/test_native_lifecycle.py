# SPDX-License-Identifier: MPL-2.0
"""Run the real Frida controller against an owned Python helper process tree."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.native
def test_real_frida_follows_game_children_and_releases_unrelated_helper(
    native_project, tmp_path
):
    root, client = native_project.root, native_project.client
    # A separate executable path distinguishes our simulated game from its controller.
    runtime = tmp_path / "synthetic game with spaces"
    if os.name == "nt":
        shutil.copytree(client.parent, runtime)
    else:
        runtime.mkdir()
        shutil.copy2(native_project.target, runtime / native_project.target.name)
    target = runtime / native_project.target.name
    output = tmp_path / "results"
    output.mkdir()
    helper = tmp_path / "game_helper.py"
    shutil.copyfile(FIXTURES / "native_lifecycle_helper.py", helper)
    probe = root / "launcher/lifecycle_probe.py"
    shutil.copyfile(FIXTURES / "native_lifecycle_probe.py", probe)
    shutil.copyfile(
        FIXTURES.parents[1] / "fixtures/native_test_binding.py",
        root / "launcher/native_test_binding.py",
    )
    arguments = ['space and "quotes"', "", "Unicode \u00e9", "--game-option", "x&y"]
    environment = os.environ.copy()
    environment["TPM_TEST_SENTINEL"] = "inherited from launcher"
    command = [
        str(client),
        "-I",
        "-S",
        "-B",
        str(probe),
        str(target),
        "-B",
        str(helper),
        "parent",
        str(output),
        str(client),
        *arguments,
    ]
    try:
        for _ in range(2):
            # Require each helper to produce fresh results rather than reuse the preceding launch's files.
            for name in ("helper.json", "replacement.json"):
                (output / name).unlink(missing_ok=True)
            # Existing content must be replaced even when using the same log and lock paths.
            log_path = root / "logs/native-test.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("previous-launch-content\n", encoding="utf-8")
            process = subprocess.Popen(
                command,
                env=environment,
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
                pytest.fail("Native controller timed out: " + stdout + stderr)
            assert process.returncode == 0, stdout + stderr
            replacement = json.loads(
                (output / "replacement.json").read_text(encoding="utf-8")
            )
            assert replacement == {
                "argv": arguments,
                "cwd": str(runtime),
                "sentinel": "inherited from launcher",
            }
            assert (output / "helper.json").exists()
            log = log_path.read_text()
            assert "previous-launch-content" not in log
            # Linux gates the two fork transitions as well as attaching to both game images.
            assert log.count("ATTACHED pid=") == (4 if os.name == "posix" else 2)
            assert log.count("HOOK MANAGER READY pid=") == 2
            assert "HELPER pid=" in log and "resumed without attachment" in log
            assert log.count("GAME SESSION ENDED") == 1 and "CLEANUP ERROR" not in log
            assert "CONTROLLER STOPPED; cleanup complete" in log
    finally:
        probe.unlink(missing_ok=True)
