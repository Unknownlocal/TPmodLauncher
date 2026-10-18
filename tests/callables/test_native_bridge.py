# SPDX-License-Identifier: MPL-2.0
"""Native callable integration has its own suite and owned helper processes."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.native
def test_private_guards_workers_and_shutdown_restore_through_real_bridge(native_project, tmp_path):
    root, client = native_project.root, native_project.client
    runtime = tmp_path / "owned callable target"
    if os.name == "nt":
        shutil.copytree(client.parent, runtime)
    else:
        runtime.mkdir()
        shutil.copy2(native_project.target, runtime / native_project.target.name)
    target = runtime / native_project.target.name
    helper = tmp_path / "callable_helper.py"
    shutil.copyfile(FIXTURES / "native_helper.py", helper)
    probe = root / "launcher/callable_probe.py"
    shutil.copyfile(FIXTURES / "native_probe.py", probe)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "fixtures/native_test_binding.py",
                    root / "launcher/native_test_binding.py")
    output = tmp_path / "results"
    output.mkdir()
    try:
        result = subprocess.run(
            [str(client), "-I", "-S", "-B", str(probe), str(target), str(helper), str(output)],
            capture_output=True, text=True, timeout=35,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        active = json.loads((output / "active.json").read_text())
        assert active["owners"] == {"owned.guard": "ready"}
        closed = json.loads((output / "closed.json").read_text())
        assert closed["state"] == "stopped" and closed["owners"] == {}
        assert not closed["errors"]
    finally:
        probe.unlink(missing_ok=True)
