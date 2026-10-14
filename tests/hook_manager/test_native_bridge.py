# SPDX-License-Identifier: MPL-2.0
"""Inject our bridge into an owned Python process, exercising real GIL entry and restoration."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.native
def test_real_bridge_initialises_hooks_and_restores_before_detach(
    native_project, tmp_path
):
    root, client = native_project.root, native_project.client
    runtime = tmp_path / "owned Python target"
    if os.name == "nt":
        shutil.copytree(client.parent, runtime)
    else:
        runtime.mkdir()
        shutil.copy2(native_project.target, runtime / native_project.target.name)
    target = runtime / native_project.target.name
    helper = tmp_path / "owned_helper.py"
    (tmp_path / "owned_native_fixture.py").write_text(
        "def method(value):\n    return value + 1\n"
    )
    shutil.copyfile(FIXTURES / "native_bridge_helper.py", helper)
    output = tmp_path / "results"
    output.mkdir()
    probe = root / "launcher/bridge_probe.py"
    shutil.copyfile(FIXTURES / "native_bridge_probe.py", probe)
    shutil.copyfile(
        FIXTURES.parents[1] / "fixtures/native_test_binding.py",
        root / "launcher/native_test_binding.py",
    )
    try:
        result = subprocess.run(
            [
                str(client),
                "-I",
                "-S",
                "-B",
                str(probe),
                str(target),
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
