# SPDX-License-Identifier: MPL-2.0
"""Exercise data-only admission, frozen attribution and shared capture ownership."""

from copy import deepcopy
from functools import partial
import json
import threading
from types import ModuleType

import pytest

from hook_manager.profiling_collector import Capture, CollectorError, FIELDS
from hook_manager.profiling_discovery import code_inventory, loaded_bindings
from hook_manager.profiling_plan import Bindings, Coordinator, PlanError
from launcher.profiling_catalogue import Catalogue, CompiledSource


GAME = {"kind": "game"}
MOD = {"kind": "mod", "id": "example.ai"}


def register(catalogue, bindings, owner=GAME, text="def update(): return 42"):
    """Emulate a trusted provider retaining verified compiled roots and namespaces."""
    root = compile(text, "src/physics.py", "exec")
    module = ModuleType("physics")
    exec(root, module.__dict__)
    keys = catalogue.replace(owner, [CompiledSource("physics", "src/physics.py", root)])
    _, codes = code_inventory((root,))
    found = loaded_bindings(module, codes)
    for key in keys:
        discriminator = catalogue.rows[key]["discriminator"]
        if discriminator in found:
            token = bindings.admit(key, codes[discriminator], found[discriminator])
            catalogue.bind(key, token, runtime_kind=bindings.runtime_kind, metrics=["calls", "elapsed"])
    return module, keys


class Collector:
    """Lifecycle probe only; timing arithmetic is tested against the real backend."""
    def __init__(self, codes, *, limits):
        self.codes, self.starts, self.stopped = codes, 0, False
        self.cleanup_fault = False
        self.data = dict(rows=[], windows=[{"lap": 1}], failures=[])

    def start(self):
        self.starts += 1

    def stop(self):
        self.stopped = True
        if self.cleanup_fault:
            self.cleanup_fault = False
            self.data["failures"] = ["monitoring_cleanup_failed"]
            raise CollectorError("cleanup failed")

    def advance_lap(self, lap):
        self.data["windows"].append({"lap": lap})
        return True

    def finishing(self):
        return self.advance_lap(None)

    def pause(self, paused):
        pass

    def snapshot(self):
        return deepcopy(self.data)


@pytest.fixture
def rig():
    catalogue = Catalogue("process-one")
    bindings = Bindings("process-one", "game-one")
    module, keys = register(catalogue, bindings)
    coordinator = Coordinator(bindings, "race-one", "main-thread", "formula", "watched", capture_factory=Collector)
    return catalogue, bindings, module, keys[0], coordinator


def test_catalogue_resolution_is_detached_data_and_reports_unmatched_selectors(rig):
    catalogue, _, _, key, _ = rig
    selection = catalogue.resolve([{"owner": GAME}, {"owner": GAME, "qualified_name": "missing"}])
    assert json.loads(json.dumps(selection)) == selection
    assert selection["functions"][0]["binding_token"] == catalogue.bindings[key]
    selection["functions"][0]["owner"]["kind"] = "changed"
    assert catalogue.rows[key]["owner"] == GAME
    assert selection["unmatched"] == [{"owner": GAME, "qualified_name": "missing"}]
    catalogue.bind(key, object(), runtime_kind="game-python", metrics=["calls"])
    assert catalogue.resolve([{"owner": GAME}])["functions"][0]["binding_token"] is None


def test_broad_selection_is_refused_instead_of_truncated():
    catalogue = Catalogue("process-one")
    root = compile("\n".join(f"def update_{i}(): pass" for i in range(257)), "physics.py", "exec")
    catalogue.replace(GAME, [CompiledSource("physics", "physics.py", root)])
    with pytest.raises(ValueError, match="Capture function limit"):
        catalogue.resolve([{"owner": GAME}])


def test_shared_consumers_install_once_and_release_independently(rig):
    catalogue, _, _, _, coordinator = rig
    selection = catalogue.resolve([{"owner": GAME}])
    first = coordinator.acquire("diagnostics.one", selection, delivery="summary")
    record = selection["functions"][0]
    catalogue.bind(record["function_id"], json.loads(json.dumps(record["binding_token"])),
                   runtime_kind="game-python", metrics=["calls", "elapsed"])
    second = coordinator.acquire("diagnostics.two", catalogue.resolve([{"owner": GAME}]*2), delivery="full")
    with pytest.raises(PlanError, match="completion"):
        coordinator.snapshot("diagnostics.one", first)
    coordinator.start()
    assert coordinator._capture.starts == 1
    coordinator.release("diagnostics.one", first)
    assert not coordinator._capture.stopped
    coordinator.finish()
    data = coordinator.snapshot("diagnostics.two", second)
    assert data["capture"] == coordinator.capture_id and data["delivery"] == "full"
    assert not data["incomplete"]
    with pytest.raises(PlanError, match="handle"):
        coordinator.snapshot("diagnostics.one", first)


@pytest.mark.parametrize("difference", ["targets", "metrics", "coverage", "missing"])
def test_incompatible_requests_cannot_widen_or_change_reserved_plan(rig, difference):
    catalogue, bindings, _, _, coordinator = rig
    register(catalogue, bindings, MOD)
    selection = catalogue.resolve([{"owner": GAME}])
    coordinator.acquire("diagnostics.one", selection)
    options = {}
    if difference == "targets":
        selection = catalogue.resolve([{"owner": GAME}, {"owner": MOD}])
    elif difference == "metrics":
        options["metrics"] = ["calls"]
    else:
        options["coverage"] = "allow_partial"
        if difference == "missing":
            selection = catalogue.resolve([{"owner": GAME}, {"owner": GAME, "qualified_name": "missing"}])
    with pytest.raises(PlanError) as error:
        coordinator.acquire("diagnostics.two", selection, **options)
    assert error.value.code == "incompatible_capture" and coordinator._capture is None
    coordinator.finish()


def test_partial_coverage_declares_missing_and_never_admits_an_empty_plan(rig):
    catalogue, _, _, _, coordinator = rig
    selection = catalogue.resolve([{"owner": GAME}, {"owner": MOD}])
    with pytest.raises(PlanError) as error:
        coordinator.acquire("diagnostics.one", selection)
    assert error.value.code == "not_ready"
    handle = coordinator.acquire("diagnostics.one", selection, coverage="allow_partial")
    coordinator.start()
    coordinator.finish()
    data = coordinator.snapshot("diagnostics.one", handle)
    assert data["incomplete"] and data["missing"] == [{"selector": {"owner": MOD}, "reason": "unknown_target"}]
    other = Coordinator(coordinator.bindings, "race-two", "main-thread", "formula", "watched", capture_factory=Collector)
    with pytest.raises(PlanError) as error:
        other.acquire("diagnostics.one", catalogue.resolve([{"owner": MOD}]), coverage="allow_partial")
    assert error.value.code == "unknown_target"


def test_partial_coverage_does_not_claim_guest_internal_support(rig):
    catalogue, bindings, _, _, coordinator = rig
    _, (key,) = register(catalogue, bindings, MOD)
    catalogue.bind(key, catalogue.bindings[key], runtime_kind="guest-python", metrics=["calls", "elapsed"])
    selection = catalogue.resolve([{"owner": GAME}, {"owner": MOD}])
    handle = coordinator.acquire("diagnostics.one", selection, coverage="allow_partial")
    coordinator.start()
    coordinator.finish()
    data = coordinator.snapshot("diagnostics.one", handle)
    assert data["incomplete"] and data["missing"][0]["reason"] == "unsupported_runtime"
    assert data["missing"][0]["function"]["owner"] == MOD
    assert [record["owner"] for record in data["functions"]] == [GAME]


@pytest.mark.parametrize("change", ["replace", "remove", "code"])
def test_selected_binding_loss_stops_capture_and_keeps_frozen_identity(rig, change):
    catalogue, bindings, module, key, coordinator = rig
    handle = coordinator.acquire("diagnostics.one", catalogue.resolve([{"owner": GAME}]))
    coordinator.start()
    coordinator.advance_lap(2)
    coordinator._capture.data["rows"] = [(0, 0, 1, 1, 0, 0, 0, 0, 100, 80)]
    if change == "replace":
        register(catalogue, bindings)
        assert catalogue.rows[key]["binding_generation"] == "2"
    elif change == "remove":
        bindings.remove(key)
    else:
        module.update.__code__ = (lambda: 7).__code__
        assert not coordinator.check()
    assert coordinator._capture.stopped
    assert not coordinator.advance_lap(3)
    with pytest.raises(PlanError, match="completion"):
        coordinator.snapshot("diagnostics.one", handle)
    coordinator.finish()
    data = coordinator.snapshot("diagnostics.one", handle)
    assert data["functions"][0]["binding_generation"] == "1"
    assert data["timings"] == [(0, key, "1", 1, 1, 0, 0, 0, 0, 100, 80)]
    assert data["losses"] == [{"function_id": key, "binding_generation": "1", "window": 1}]
    assert data["failures"] == ["binding_changed"] and data["incomplete"]


def test_stale_plan_is_refused_before_installing_a_collector(rig):
    catalogue, bindings, _, key, coordinator = rig
    handle = coordinator.acquire("diagnostics.one", catalogue.resolve([{"owner": GAME}]))
    bindings.remove(key)
    with pytest.raises(PlanError, match="entry"):
        coordinator.start()
    assert coordinator._capture is None
    coordinator.finish()
    data = coordinator.snapshot("diagnostics.one", handle)
    assert data["timings"] == [] and data["windows"] == [] and data["incomplete"]


def test_unselected_changes_and_mutation_of_selection_do_not_change_frozen_results(rig):
    catalogue, bindings, _, _, coordinator = rig
    selection = catalogue.resolve([{"owner": GAME}])
    handle = coordinator.acquire("diagnostics.one", selection, metrics=["calls"])
    selection["functions"][0]["qualified_name"] = "changed"
    coordinator.start()
    register(catalogue, bindings, MOD)
    assert coordinator.check() and not coordinator._capture.stopped
    coordinator._capture.data["rows"] = [(0, 0, 1, 1, 0, 0, 0, 0, 100, 100)]
    coordinator.finish()
    first = coordinator.snapshot("diagnostics.one", handle)
    assert first["functions"][0]["qualified_name"] == "update" and first["fields"] == FIELDS[:6]
    assert len(first["timings"][0]) == 9
    first["windows"][0]["lap"] = 99
    first["functions"][0]["owner"]["kind"] = "changed"
    assert coordinator.snapshot("diagnostics.one", handle)["windows"] == [{"lap": 1}]
    assert coordinator.snapshot("diagnostics.one", handle)["functions"][0]["owner"] == GAME


def test_runtime_reservation_cleanup_failure_and_retry(rig):
    catalogue, bindings, _, _, coordinator = rig
    selection = catalogue.resolve([{"owner": GAME}])
    handle = coordinator.acquire("diagnostics.one", selection)
    other = Coordinator(bindings, "race-two", "main-thread", "formula", "watched", capture_factory=Collector)
    coordinator.start()
    coordinator._capture.cleanup_fault = True
    with pytest.raises(CollectorError, match="cleanup"):
        coordinator.finish()
    with pytest.raises(PlanError) as error:
        other.acquire("diagnostics.two", selection)
    assert error.value.code == "incompatible_capture"
    coordinator.finish()
    assert coordinator.snapshot("diagnostics.one", handle)["failures"] == ["monitoring_cleanup_failed"]
    other.acquire("diagnostics.two", selection)
    other.finish()


def test_handle_ownership_and_lifetime_reservation_bounds(rig):
    catalogue, _, _, _, coordinator = rig
    coordinator._maximum = 1
    selection = catalogue.resolve([{"owner": GAME}])
    handle = coordinator.acquire("diagnostics.one", selection)
    with pytest.raises(PlanError) as error:
        coordinator.acquire("diagnostics.two", selection)
    assert error.value.code == "resource_limit"
    with pytest.raises(PlanError, match="handle"):
        coordinator.release("diagnostics.two", handle)
    coordinator.release("diagnostics.one", handle)
    with pytest.raises(PlanError, match="handle"):
        coordinator.snapshot("diagnostics.one", handle)
    with pytest.raises(PlanError, match="precede"):
        coordinator.acquire("diagnostics.one", selection)
    coordinator.finish()


@pytest.mark.parametrize("change", ["process", "runtime", "metrics"])
def test_current_process_and_supported_metrics_are_required(rig, change):
    catalogue, _, _, key, coordinator = rig
    if change == "runtime":
        catalogue.bind(key, catalogue.bindings[key], runtime_kind="guest-python", metrics=["calls", "elapsed"])
    elif change == "metrics":
        catalogue.bind(key, catalogue.bindings[key], runtime_kind="game-python", metrics=["calls"])
    selection = catalogue.resolve([{"owner": GAME}])
    if change == "process":
        selection["process"] = "process-old"
    with pytest.raises(PlanError) as error:
        coordinator.acquire("diagnostics.one", selection)
    assert error.value.code == ("stale_catalogue" if change == "process" else "not_ready")
    assert coordinator._capture is None


def test_failed_start_preserves_error_and_releases_reservation_at_race_end(rig):
    catalogue, bindings, _, _, coordinator = rig
    error = RuntimeError("backend unavailable")
    def unavailable(*args, **kwargs):
        raise error
    coordinator._factory = unavailable
    handle = coordinator.acquire("diagnostics.one", catalogue.resolve([{"owner": GAME}]))
    with pytest.raises(RuntimeError) as caught:
        coordinator.start()
    assert caught.value is error
    assert bindings._lease is coordinator
    coordinator.finish()
    data = coordinator.snapshot("diagnostics.one", handle)
    assert data["failures"] == ["collector_start_failed"] and data["incomplete"]
    assert bindings._lease is None


def test_registry_identity_admission_and_thread_ownership(rig):
    catalogue, bindings, module, key, coordinator = rig
    token = bindings.admit(key, module.update.__code__, [module.update])
    assert token == catalogue.bindings[key]
    with pytest.raises(PlanError) as error:
        bindings.admit("f"*64, module.update.__code__, [module.update])
    assert error.value.code == "ambiguous_binding"
    outcomes = []
    def worker():
        try:
            coordinator.acquire("diagnostics.one", catalogue.resolve([{"owner": GAME}]))
        except PlanError as error:
            outcomes.append(error.code)
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(5)
    assert not thread.is_alive() and outcomes == ["wrong_context"]


@pytest.mark.native
def test_native_binding_loss_inside_target_truncates_reporting_and_preserves_return(native):
    catalogue = Catalogue("process-one")
    bindings = Bindings("process-one", "game-one")
    module, (key,) = register(catalogue, bindings, text="def update():\n    remove()\n    return 42")
    module.remove = lambda: bindings.remove(key)
    coordinator = Coordinator(bindings, "race-one", "main-thread", "formula", "watched",
                              capture_factory=partial(Capture, backend=native))
    handle = coordinator.acquire("diagnostics.one", catalogue.resolve([{"owner": GAME}]))
    coordinator.start()
    try:
        assert module.update() == 42
    finally:
        coordinator.finish()
    data = coordinator.snapshot("diagnostics.one", handle)
    assert data["failures"] == ["binding_changed"] and data["incomplete"]
    assert data["timings"][0][3:8] == (1, 0, 0, 0, 1)


@pytest.mark.native
def test_native_simulated_worker_enrolls_and_exports_on_its_own_context(native):
    result = {}
    def worker():
        try:
            catalogue = Catalogue("process-worker")
            bindings = Bindings("process-worker", "game-worker")
            module, keys = register(catalogue, bindings)
            coordinator = Coordinator(bindings, "race-one", "worker-thread", "formula", "simulated",
                                      capture_factory=partial(Capture, backend=native))
            handle = coordinator.acquire("diagnostics.one", catalogue.resolve([{"owner": GAME}]))
            coordinator.start()
            try:
                for _ in range(3):
                    assert module.update() == 42
            finally:
                coordinator.finish()
            result["data"] = coordinator.snapshot("diagnostics.one", handle)
        except BaseException as error:
            result["error"] = error
    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(5)
    assert not thread.is_alive() and "error" not in result, result
    data = result["data"]
    assert data["mode"] == "simulated" and data["context"] == "worker-thread" and not data["incomplete"]
    assert data["timings"][0][3:5] == (3, 3)


@pytest.mark.native
def test_native_game_and_mod_functions_share_attributed_lap_capture(native):
    catalogue = Catalogue("process-one")
    bindings = Bindings("process-one", "game-one")
    game, game_keys = register(catalogue, bindings)
    mod, mod_keys = register(catalogue, bindings, MOD, "def update(): return game()")
    mod.game = game.update
    coordinator = Coordinator(bindings, "race-one", "main-thread", "formula", "watched",
                              capture_factory=partial(Capture, backend=native))
    selection = catalogue.resolve([{"owner": GAME}, {"owner": MOD}])
    first = coordinator.acquire("diagnostics.one", selection)
    second = coordinator.acquire("diagnostics.two", selection, delivery="full")
    coordinator.start()
    try:
        assert mod.update() == 42
        coordinator.advance_lap(2)
        coordinator.release("diagnostics.one", first)
        assert mod.update() == 42
        coordinator.finishing()
        assert game.update() == 42
    finally:
        coordinator.finish()
    data = coordinator.snapshot("diagnostics.two", second)
    assert not data["incomplete"]
    assert {row["owner"]["kind"] for row in data["functions"]} == {"game", "mod"}
    rows = {(row[0], row[1]): row[3:] for row in data["timings"]}
    assert rows[0, game_keys[0]][:2] == rows[0, mod_keys[0]][:2] == (1, 1)
    assert rows[1, game_keys[0]][:2] == rows[1, mod_keys[0]][:2] == (1, 1)
    assert rows[2, game_keys[0]][:2] == (1, 1)
    assert [window["lap"] for window in data["windows"]] == [1, 2, None]
