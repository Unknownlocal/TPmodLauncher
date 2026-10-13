# SPDX-License-Identifier: MPL-2.0
"""Inject our bridge into an owned Python process, exercising real GIL entry and restoration."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.embedded
def test_real_bridge_initialises_hooks_and_restores_before_detach(
    embedded_project, tmp_path
):
    root = embedded_project
    client = root / "libs/python-3.13.16-embed-amd64/python.exe"
    runtime = tmp_path / "owned Python target"
    shutil.copytree(client.parent, runtime)
    helper = tmp_path / "owned_helper.py"
    (tmp_path / "owned_native_fixture.py").write_text(
        "def method(value):\n    return value + 1\n"
    )
    shutil.copyfile(FIXTURES / "native_bridge_helper.py", helper)
    output = tmp_path / "results"
    output.mkdir()
    probe = root / "launcher/bridge_probe.py"
    shutil.copyfile(FIXTURES / "native_bridge_probe.py", probe)
    try:
        result = subprocess.run(
            [
                str(client),
                "-B",
                str(probe),
                str(runtime / "python.exe"),
                str(helper),
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        active = json.loads((output / "active.json").read_text())
        assert active["value"] == 9 and active["status"]["groups"] == {
            "owned": "active"
        }
        closed = json.loads((output / "closed.json").read_text())
        assert closed["value"] == 4 and closed["status"]["state"] == "stopped"
        assert (
            closed["status"]["groups"] == {"owned": "removed"}
            and closed["observer_removed"]
        )
        assert not closed["status"]["errors"]
    finally:
        probe.unlink(missing_ok=True)
