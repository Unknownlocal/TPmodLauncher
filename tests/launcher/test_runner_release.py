# SPDX-License-Identifier: MPL-2.0
"""Verify the installed runner before entry creates any game session."""

from contextlib import closing
import threading
import time
from types import SimpleNamespace

import pytest

from launcher import entry, game_bridge, process_lifecycle
from mod_runtime.bundle import DIRECTORY


@pytest.fixture
def installed(tmp_path, runner_bundle, monkeypatch):
    root = tmp_path / "game/modLauncher"
    runner_bundle.copy_to(root / DIRECTORY)
    executable = root.parent / "TeamPrincipal.exe"
    executable.write_bytes(b"owned inert executable fixture")
    monkeypatch.setattr(entry, "__file__", str(root / "launcher/entry.py"))
    # Only external dependency setup is replaced; artifact verification and mod loading are real.
    monkeypatch.setattr(entry._dependencies, "import_dependencies", lambda root: object())
    monkeypatch.setattr(entry._dependencies, "load_source_packages", lambda root: None)
    monkeypatch.setattr(entry, "version", lambda name: "owned-fixture")
    return root, executable


@pytest.mark.parametrize("check_only,damage", [
    pytest.param(False, "missing", id="launch-missing"),
    pytest.param(True, "component", id="check-only-changed"),
])
def test_entry_rejects_runner_damage_before_launch_or_cache(installed, monkeypatch, capsys, check_only, damage):
    root, executable = installed
    if damage == "missing":
        (root / DIRECTORY / "bundle.json").unlink()
    else:
        (root / DIRECTORY / "runner.wasm").write_bytes(b"damaged")
    def forbidden(*args, **kwargs):
        pytest.fail("Runner rejection must precede game launch")
    monkeypatch.setattr(process_lifecycle, "run_launcher", forbidden)
    arguments = ["--check-only"] if check_only else ["--", str(executable)]
    assert entry.main(arguments) == 1
    assert "Python runner verification failed" in capsys.readouterr().err
    assert not any((root / name).exists() for name in ("bin", "logs", ".state", ".cache"))


def test_entry_supplies_installed_runner_to_real_source_pack(installed, source_mod, monkeypatch, python_compilation_cache):
    from launcher import compilation_cache
    # Startup uses real compiled code; cache-policy tests own cold compilation.
    monkeypatch.setattr(compilation_cache, "CompilationCache", lambda root: python_compilation_cache)
    root, executable = installed
    game = root.parent
    folder = game / "data/owned pack"
    source_mod(folder, "owned.source")
    messages, progress = [], []
    bridge = SimpleNamespace(wake=lambda: None, set_menu=lambda items: None, set_loading=progress.append,
                             set_settings=lambda records, result=None: None, set_themes=lambda records: None)
    monkeypatch.setattr(game_bridge, "factory", lambda *args: object())
    def run(frida, command, *args, mods_factory, **kwargs):
        assert command == [str(executable), "owned-argument"]
        with closing(mods_factory(messages.append, bridge)) as scope:
            assert scope.runner_bundle.directory == root / DIRECTORY
            scope.load(dict(state="ready", series="formula", id=folder.name, root=str(folder.resolve())))
            deadline = time.monotonic() + 30
            while scope.loading is not None:
                assert time.monotonic() < deadline
                scope.poll()
                threading.Event().wait(0.002)
            mod = scope.mods["owned.source"]
            assert mod.event("owned.event", {"ok": True}) == {"ok": True}
            assert progress[-1] == dict(state="complete", loaded=1, failed=0, total=1)
        assert mod.sandbox.closed
        return 0
    monkeypatch.setattr(process_lifecycle, "run_launcher", run)
    assert entry.main(["--", str(executable), "owned-argument"]) == 0
    assert any('"message": "source ready"' in line for line in messages)
