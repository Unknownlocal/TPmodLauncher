# SPDX-License-Identifier: MPL-2.0
"""Verify the installed runner before entry creates any game session."""

import json

import pytest

from launcher import entry, process_lifecycle
from mod_runtime.bundle import DIRECTORY


def test_entry_rejects_runner_damage_before_launch_or_cache(tmp_path, runner_bundle, monkeypatch, capsys):
    root = tmp_path / "game/modLauncher"
    runner_bundle.copy_to(root / DIRECTORY)
    executable = root.parent / "TeamPrincipal.exe"
    executable.write_bytes(b"owned inert executable fixture")
    monkeypatch.setattr(entry, "__file__", str(root / "launcher/entry.py"))
    # External dependency setup is replaced; runner verification is real.
    monkeypatch.setattr(entry._dependencies, "import_dependencies", lambda root: object())
    monkeypatch.setattr(entry._dependencies, "load_source_packages", lambda root: None)
    (root / DIRECTORY / "bundle.json").unlink()
    def forbidden(*args, **kwargs):
        pytest.fail("Runner rejection must precede game launch")
    monkeypatch.setattr(process_lifecycle, "run_launcher", forbidden)
    outcome = {}
    assert entry.main(["--", str(executable)], outcome=outcome) == 1
    assert "Python runner verification failed" in capsys.readouterr().err
    text = (root / "logs/launcher.log").read_text(encoding="utf-8")
    saved = json.loads(text.rpartition("LAUNCH FAILURE DETAILS ")[2])
    assert saved["failure"] == outcome["failure"] and saved["session_id"] == outcome["session_id"]
    assert outcome["log_written"]
    assert outcome["failure"]["stage"] == "runner_verification" and "Traceback" in text
    assert not any((root / name).exists() for name in ("bin", ".cache"))
