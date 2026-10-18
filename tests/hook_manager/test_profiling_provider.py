# SPDX-License-Identifier: MPL-2.0
"""Verify code provenance, runtime withdrawals and stale-safe catalogue updates."""

from copy import deepcopy
from functools import partial
import json
import marshal
from types import FunctionType, ModuleType

import pytest

from hook_manager.profiling_collector import Capture
from hook_manager.profiling_discovery import DiscoveryError
from hook_manager.profiling_plan import Bindings, Coordinator, PlanError
from hook_manager.profiling_provider import Provider
from hook_manager import profiling_provider as provider_module
from launcher.profiling_catalogue import Catalogue, CatalogueError, CompiledSource


GAME = {"kind": "game"}


def loaded(root):
    # A normal loader and an archive inspector decode separate code objects.
    # Matching must verify their contents without requiring pointer equality.
    module = ModuleType("physics")
    exec(marshal.loads(marshal.dumps(root)), module.__dict__)
    return module


@pytest.fixture
def rig():
    root = compile("def update(): return 42\ndef other(): return 7", "physics.py", "exec")
    catalogue = Catalogue("process-one")
    catalogue.replace(GAME, [CompiledSource("physics", "physics.py", root)])
    bindings = Bindings("process-one", "game-one")
    return catalogue, bindings, Provider(bindings), root, loaded(root)


def selection(catalogue):
    return catalogue.resolve([{"owner": GAME}])


def refresh(rig):
    catalogue, _, provider, root, module = rig
    return provider.refresh(selection(catalogue), root, module)


def test_separately_loaded_code_gets_data_only_idempotent_readiness(rig):
    catalogue, bindings, provider, root, module = rig
    assert module.update.__code__ is not root.co_consts[0]
    report = json.loads(json.dumps(refresh(rig)))
    assert {update["availability"] for update in report["bindings"]} == {"ready"}
    generations = catalogue.apply_bindings(report)
    epoch = catalogue.generation
    assert set(generations.values()) == {"1"}
    assert catalogue.apply_bindings(report) == generations and catalogue.generation == epoch
    assert catalogue.apply_bindings(refresh(rig)) == generations and catalogue.generation == epoch
    for row in selection(catalogue)["functions"]:
        assert row["binding_token"] == bindings._entries[row["function_id"]].token


def test_foreign_alias_names_and_wrapped_metadata_do_not_prove_provenance(rig):
    catalogue, bindings, provider, root, module = rig
    foreign = loaded(root)
    module.update = foreign.update
    module.update.__module__, module.update.__qualname__ = "physics", "update"
    # The bytes match, but the function was not loaded in this module's globals.
    report = refresh(rig)
    catalogue.apply_bindings(report)
    records = {row["qualified_name"]: row for row in selection(catalogue)["functions"]}
    assert records["update"]["availability"] == "not_loaded"
    assert records["other"]["availability"] == "ready"
    exec("def update(): return 0", module.__dict__)
    module.update.__wrapped__ = foreign.update
    assert next(update for update in refresh(rig)["bindings"] if update["function_id"] == records["update"]["function_id"])["binding_token"] is None


def test_replacement_advances_generation_and_delayed_report_cannot_restore_old_code(rig):
    catalogue, bindings, provider, root, module = rig
    old = refresh(rig)
    catalogue.apply_bindings(old)
    # Executing the verified code in the original globals makes this an actual
    # replacement within that loaded module, not an imported alias.
    exec(marshal.loads(marshal.dumps(root)), module.__dict__)
    new = refresh(rig)
    assert set(catalogue.apply_bindings(new).values()) == {"2"}
    before = deepcopy(catalogue.rows)
    with pytest.raises(CatalogueError) as error:
        catalogue.apply_bindings(old)
    assert error.value.code == "stale_catalogue" and catalogue.rows == before


def test_unload_withdraws_runtime_before_catalogue_owner_replacement(rig):
    catalogue, bindings, provider, root, module = rig
    catalogue.apply_bindings(refresh(rig))
    report = provider.invalidate(selection(catalogue))
    assert not bindings._entries
    catalogue.apply_bindings(report)
    assert all(row["availability"] == "not_loaded" and row["reason"] == "binding_removed" for row in catalogue.rows.values())
    catalogue.replace(GAME, [])
    with pytest.raises(CatalogueError, match="Unknown"):
        catalogue.apply_bindings(report)
    assert not catalogue.rows


@pytest.mark.parametrize("fault", ["generation", "artifact", "process", "fields"])
def test_report_batch_validates_every_update_before_mutation(rig, fault):
    catalogue, bindings, provider, root, module = rig
    report = refresh(rig)
    damaged = deepcopy(report)
    if fault == "generation":
        damaged["bindings"][-1]["expected_generation"] = "9"
    elif fault == "artifact":
        damaged["bindings"][-1]["artifact_checksum"] = "f"*64
    elif fault == "process":
        damaged["process"] = "process-retired"
    else:
        damaged["bindings"][-1]["extra"] = True
    before = deepcopy(catalogue.rows)
    with pytest.raises(ValueError):
        catalogue.apply_bindings(damaged)
    assert catalogue.rows == before and not catalogue.bindings and catalogue.generation == 1
    catalogue.apply_bindings(report)
    assert all(row["availability"] == "ready" for row in catalogue.rows.values())


def test_failed_inventory_or_budget_check_publishes_no_runtime_prefix(rig):
    catalogue, bindings, provider, root, module = rig
    records = selection(catalogue)
    records["functions"][-1]["qualified_name"] = "wrong-inventory"
    with pytest.raises(PlanError, match="inventory"):
        provider.refresh(records, root, module)
    assert not bindings._entries
    with pytest.raises(DiscoveryError, match="byte limit"):
        Provider(bindings, code_bytes=1).refresh(selection(catalogue), root, module)
    assert not bindings._entries
    limited = Bindings("process-one", "limited-runtime", maximum=1)
    with pytest.raises(PlanError, match="binding limit"):
        Provider(limited).refresh(selection(catalogue), root, module)
    assert not limited._entries


def test_different_root_body_cannot_reuse_admitted_source_identity(rig):
    catalogue, bindings, provider, root, module = rig
    catalogue.apply_bindings(refresh(rig))
    # Names, lines and constant positions still agree. Both supplied root and
    # loaded code changed, so only the host's complete source identity catches it.
    changed = compile("def update(): return 99\ndef other(): return 7", "physics.py", "exec")
    with pytest.raises(PlanError, match="admitted compiled source"):
        provider.refresh(selection(catalogue), changed, loaded(changed))
    assert not bindings._entries


def test_ambiguous_equal_live_code_is_not_silently_merged(rig):
    catalogue, bindings, provider, root, module = rig
    module.alias = FunctionType(module.update.__code__.replace(), module.__dict__)
    report = refresh(rig)
    catalogue.apply_bindings(report)
    records = {row["qualified_name"]: row for row in catalogue.rows.values()}
    assert records["update"]["availability"] == "unsupported" and records["update"]["reason"] == "ambiguous_code"
    assert records["other"]["availability"] == "ready"


def test_code_change_during_fingerprinting_cannot_promote_unverified_code(rig, monkeypatch):
    catalogue, bindings, provider, root, module = rig
    original = marshal.dumps
    old_code = module.update.__code__
    def changing(code, version):
        content = original(code, version)
        if code is old_code:
            module.update.__code__ = (lambda: 99).__code__
        return content
    monkeypatch.setattr(provider_module.marshal, "dumps", changing)
    catalogue.apply_bindings(refresh(rig))
    update = next(row for row in catalogue.rows.values() if row["qualified_name"] == "update")
    assert update["reason"] == "binding_changed" and update["function_id"] not in bindings._entries


def test_namespace_change_during_verification_withdraws_previously_ready_bindings(rig, monkeypatch):
    catalogue, bindings, provider, root, module = rig
    catalogue.apply_bindings(refresh(rig))
    original = marshal.dumps
    old_code = module.update.__code__
    def changing(code, version):
        content = original(code, version)
        if code is old_code:
            module.update = FunctionType((lambda: 99).__code__, module.__dict__)
        return content
    monkeypatch.setattr(provider_module.marshal, "dumps", changing)
    with pytest.raises(DiscoveryError, match="Namespace changed"):
        refresh(rig)
    assert not bindings._entries


def test_equal_expected_inventory_entries_and_legacy_token_equality_are_safe(rig):
    catalogue, bindings, provider, root, module = rig
    class Token:
        def __eq__(self, other):
            raise AssertionError("Do not call legacy token equality")
    key = next(iter(catalogue.rows))
    catalogue.bind(key, Token(), runtime_kind="game-python", metrics=["calls", "elapsed"])
    catalogue.apply_bindings(refresh(rig))
    # Separate equal code entries in an artifact have different discriminators;
    # a separately loaded copy cannot choose between them by content alone.
    child = root.co_consts[0]
    ambiguous = root.replace(co_consts=(child, child.replace(), None))
    provider.invalidate(selection(catalogue))
    catalogue.replace(GAME, [CompiledSource("physics", "physics.py", ambiguous)])
    report = provider.refresh(selection(catalogue), ambiguous, loaded(ambiguous))
    assert {row["reason"] for row in report["bindings"]} == {"ambiguous_code"}


def test_resumable_and_not_yet_created_functions_stay_visible_but_unavailable():
    root = compile("def gen(): yield 1\ndef outer():\n    def inner(): return 42\n    return inner", "physics.py", "exec")
    catalogue = Catalogue("process-one")
    catalogue.replace(GAME, [CompiledSource("physics", "physics.py", root)])
    bindings = Bindings("process-one", "game-one")
    catalogue.apply_bindings(Provider(bindings).refresh(selection(catalogue), root, loaded(root)))
    rows = {row["qualified_name"]: row for row in catalogue.rows.values()}
    assert rows["gen"]["reason"] == "resumable_unsupported"
    assert rows["outer.<locals>.inner"]["reason"] == "no_live_binding"
    assert rows["outer"]["availability"] == "ready"


@pytest.mark.native
@pytest.mark.parametrize("loss", ["code_changed", "verification_failed"])
def test_verified_provider_relay_drives_capture_and_loss_stops_it(native, rig, loss):
    catalogue, bindings, provider, root, module = rig
    catalogue.apply_bindings(json.loads(json.dumps(refresh(rig))))
    coordinator = Coordinator(bindings, "race-one", "main-thread", "formula", "watched",
                              capture_factory=partial(Capture, backend=native))
    handle = coordinator.acquire("diagnostics.one", selection(catalogue))
    coordinator.start()
    try:
        assert module.update() == 42
        coordinator.advance_lap(2)
        assert module.update() == 42
        # The callable stays in its namespace while its code no longer matches
        # the artifact. Provider withdrawal stops the old capture before relay.
        if loss == "code_changed":
            module.update.__code__ = (lambda: 99).__code__
            report = refresh(rig)
            catalogue.apply_bindings(report)
        else:
            with pytest.raises(DiscoveryError, match="byte limit"):
                Provider(bindings, code_bytes=1).refresh(selection(catalogue), root, module)
        assert coordinator._capture._stopped
        assert module.update() == (99 if loss == "code_changed" else 42)
    finally:
        coordinator.finish()
    data = coordinator.snapshot("diagnostics.one", handle)
    assert data["incomplete"] and data["failures"] == ["binding_changed"]
    assert sum(row[3] for row in data["timings"]) == 2
    assert all(row[2] == "1" for row in data["timings"])
