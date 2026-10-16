# SPDX-License-Identifier: MPL-2.0
"""Selected-pack source admission, real runner execution and native readiness."""

from contextlib import closing
import json
from pathlib import Path
import threading
import time
import sys

import pytest

from hook_manager.menu import MenuAdapter
from launcher.pack_mods import PackMods
from launcher.sandbox import SandboxError
from mod_runtime import preparation
from mod_runtime.source import SourceSnapshot


@pytest.fixture
def pack(tmp_path):
    folder = tmp_path / "data/owned pack"
    folder.mkdir(parents=True)
    return tmp_path, folder, dict(state="ready", series="formula", id=folder.name, root=str(folder.resolve()))


def finish(scope):
    deadline = time.monotonic() + 30
    while scope.loading is not None:
        assert time.monotonic() < deadline, "Source pack did not finish loading"
        scope.poll()
        if scope.loading is not None:
            threading.Event().wait(0.002)


def records(messages, event):
    return [json.loads(line.split(": ", 1)[1]) for line in messages if line.startswith(f"CODE MOD {event}:")]


@pytest.mark.parametrize("background", [False, True])
def test_sources_and_explicit_wasm_share_order_progress_and_timings(pack, source_mod, code_mod, runner_bundle, background):
    game, folder, record = pack
    source_mod(folder, "z-folder", mod_id="owned.a")
    code_mod(folder, "a-folder", mod_id="owned.b")
    messages, progress = [], []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle,
                          background=background, set_loading=progress.append)) as scope:
        scope.load(record)
        finish(scope)
        assert list(scope.mods) == ["owned.a", "owned.b"]
        assert scope.mods["owned.a"].event("owned.event", {"ok": True}) == {"ok": True}
        assert scope.mods["owned.a"].sandbox._thread == threading.get_ident()
        assert scope.mods["owned.b"].event("owned.event", None) == {}
        assert progress[-1] == dict(state="complete", loaded=2, failed=0, total=2)
        timing = {item["id"]: item["phase_seconds"] for item in records(messages, "TIMING")}
        assert {"manifest", "source_prepare", "source_snapshot", "runner_read", "compile", "source_load", "initialize", "activate"} <= timing["owned.a"].keys()
        assert "source_prepare" not in timing["owned.b"]
        assert records(messages, "REPORT") == [dict(id="owned.a", message="source ready")]
        instances = list(scope.mods.values())
    assert all(mod.sandbox.closed for mod in instances)
    assert [item["id"] for item in records(messages, "CLOSED")] == ["owned.b", "owned.a"]


@pytest.mark.parametrize("mixed", [False, True])
def test_duplicate_ids_reject_all_copies_before_any_source_runs(pack, source_mod, code_mod, runner_bundle, monkeypatch, mixed):
    game, folder, record = pack
    source_mod(folder, "copy-a", mod_id="owned.duplicate", code="while True: pass")
    if mixed:
        code_mod(folder, "copy-b", mod_id="owned.duplicate")
    else:
        source_mod(folder, "copy-b", mod_id="owned.duplicate")
    def forbidden(*args):
        pytest.fail("Duplicate source must not be read or executed")
    monkeypatch.setattr(SourceSnapshot, "read", forbidden)
    messages, progress = [], []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle, set_loading=progress.append)) as scope:
        scope.load(record)
        finish(scope)
        assert not scope.mods
        assert progress[-1] == dict(state="complete", loaded=0, failed=2, total=2)
        assert [item["detail"] for item in records(messages, "REJECTED")] == ["Duplicate mod ID"] * 2


@pytest.mark.parametrize("failure", ["schema", "syntax", "missing", "loop"])
def test_bad_sources_fail_individually_without_author_wasm_fallback(pack, source_mod, runner_bundle, failure):
    game, folder, record = pack
    bad = source_mod(folder, "a-bad", mod_id="owned.bad")
    if failure == "schema":
        path = bad / "mod.toml"
        path.write_text(path.read_text() + 'component = "mod.wasm"\n')
    elif failure == "syntax":
        (bad / "src/mod.py").write_text("def invalid(:")
    elif failure == "missing":
        (bad / "src/mod.py").unlink()
    else:
        (bad / "src/mod.py").write_text("while True: pass")
    source_mod(folder, "b-good", mod_id="owned.good")
    messages, progress = [], []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle, set_loading=progress.append)) as scope:
        scope.load(record)
        finish(scope)
        assert list(scope.mods) == ["owned.good"]
        assert progress[-1] == dict(state="complete", loaded=1, failed=1, total=2)
        assert len(records(messages, "REJECTED")) == 1
        assert records(messages, "REPORT") == [dict(id="owned.good", message="source ready")]


@pytest.mark.parametrize("change", ["identity", "capabilities"])
def test_manifest_changes_after_admission_are_rejected_before_compile(pack, source_mod, runner_bundle, monkeypatch, change):
    game, folder, record = pack
    mod = source_mod(folder, "owned.source")
    original = SourceSnapshot.read
    def changed(path):
        manifest = Path(path) / "mod.toml"
        content = manifest.read_text()
        content = content.replace("owned.source", "owned.changed") if change == "identity" else content.replace('["diagnostics"]', "[]")
        manifest.write_text(content)
        return original(path)
    def forbidden(*args, **kwargs):
        pytest.fail("Changed metadata must be rejected before native compilation")
    monkeypatch.setattr(SourceSnapshot, "read", changed)
    monkeypatch.setattr(preparation, "CompiledMod", forbidden)
    messages = []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle)) as scope:
        scope.load(record)
        finish(scope)
        assert not scope.mods and scope.failed == 1
        rejected = records(messages, "REJECTED")[0]
        assert rejected["id"] == "owned.source" and rejected["folder"] == mod.name
        assert "changed after admission" in rejected["detail"]
        assert records(messages, "TIMING")[0]["phase_seconds"]["source_prepare"] >= 0


def test_source_enablement_is_explicit_and_legacy_python_is_rejected(pack, source_mod, code_mod, runner_bundle):
    game, folder, record = pack
    source_mod(folder, "owned.source")
    legacy = code_mod(folder, "owned.legacy")
    path = legacy / "mod.toml"
    path.write_text(path.read_text().replace('runtime = "wasm"', 'runtime = "python"'))
    with closing(PackMods(game, lambda _: None)) as old:
        old.load(record)
        assert list(old.mods) == ["owned.legacy"] and old.failed == 1
    with closing(PackMods(game, lambda _: None, runner_bundle=runner_bundle)) as new:
        new.load(record)
        finish(new)
        assert list(new.mods) == ["owned.source"] and new.failed == 1


def test_source_menu_readiness_and_failure_rollback(pack, source_mod, runner_bundle, native_menu):
    game, folder, record = pack
    root = Path(__file__).resolve().parents[2]
    source_mod(folder, "owned.menu", code=(root / "examples/menu_mod/src/mod.py").read_text(),
               capabilities=("diagnostics", "main-menu"))
    source_mod(folder, "owned.diagnostics")
    hub, _ = native_menu
    messages = []
    with closing(MenuAdapter()) as adapter, closing(PackMods(
        game, messages.append, runner_bundle=runner_bundle,
        set_menu=lambda items: {"menu": adapter.update(items)}, set_loading=adapter.set_loading,
    )) as scope:
        scope.load(record)
        finish(scope)
        assert adapter._notice() == "Mods loaded: 2" and not adapter.status()["loading"]["blocked"]
        assert "Mod Launcher" in hub.MENU_ITEMS
        menu_mod = scope.mods["owned.menu"]
        with pytest.raises(SandboxError):
            menu_mod.event("bad/event", None)
        assert "Mod Launcher" not in hub.MENU_ITEMS and list(scope.mods) == ["owned.diagnostics"]
        assert scope.mods["owned.diagnostics"].event("owned.event", None) is None
    assert hub.MENU_ITEMS == ["Career", "Load", "Exit"]


def test_closing_source_preparation_discards_late_result_without_activation(pack, source_mod, runner_bundle, monkeypatch):
    game, folder, record = pack
    source_mod(folder, "owned.cancelled")
    entered, release = threading.Event(), threading.Event()
    results = []
    original = preparation._prepare
    def blocked(*args):
        result = original(*args)
        results.append(result)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(preparation, "_prepare", blocked)
    messages, progress = [], []
    scope = PackMods(game, messages.append, runner_bundle=runner_bundle, set_loading=progress.append)
    try:
        scope.load(record)
        assert entered.wait(30)
        scope.close()
        assert scope.closed and not scope.mods and not results[0].compiled.closed
    finally:
        release.set()
        scope.close()
        if scope.executor is not None:
            scope.executor.shutdown(wait=True)
    assert results[0].snapshot is None and results[0].compiled.closed
    assert not records(messages, "ACTIVE") and not records(messages, "REPORT")
    assert not any(item["state"] == "complete" for item in progress)


@pytest.mark.parametrize("fail", [False, True])
def test_deferred_native_menu_keeps_readiness_blocked_until_result(pack, source_mod, runner_bundle, native_menu, monkeypatch, fail):
    game, folder, record = pack
    root = Path(__file__).resolve().parents[2]
    source_mod(folder, "owned.menu", code=(root / "examples/menu_mod/src/mod.py").read_text(),
               capabilities=("diagnostics", "main-menu"))
    source_mod(folder, "owned.diagnostics")
    hub, _ = native_menu
    monkeypatch.delitem(sys.modules, hub.__name__)
    with closing(MenuAdapter()) as adapter, closing(PackMods(
        game, lambda _: None, runner_bundle=runner_bundle,
        set_menu=lambda items: {"menu": adapter.update(items)}, set_loading=adapter.set_loading,
    )) as scope:
        scope.load(record)
        finish(scope)
        assert len(scope.mods) == 2 and adapter.state == "pending"
        assert adapter.status()["loading"]["blocked"]
        if fail:
            hub.MENU_ITEMS.insert(0, "Mod Launcher")  # A UI declaration conflict can be rolled back.
        monkeypatch.setitem(sys.modules, hub.__name__, hub)
        scope.menu_status(adapter.poll())
        assert not adapter.status()["loading"]["blocked"]
        assert list(scope.mods) == (["owned.diagnostics"] if fail else ["owned.diagnostics", "owned.menu"])
        assert scope.mods["owned.diagnostics"].event("owned.event", {"ok": True}) == {"ok": True}
