# SPDX-License-Identifier: MPL-2.0
"""Dedicated real-Wasm handler dispatch and controller-owned transport lifetime."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import json
import inspect
import shutil
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.manager import HookError
from hook_manager.callables import CallBinding, CallableTarget, refuse_boolean
from hook_manager.game_adapters.regulations import RegulationChannel
from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.native_policy import NativeRegulationPolicy
from launcher.pack_mods import PackMods
from launcher.game_bridge import Bridge
from launcher.sandbox import SandboxError
from regulation_support import context, make_mod, selection, validator


# Transport-only tests supply an explicit stand-in for complete installation.
# This is not evidence that production has installed the staffing callers.
PERSONNEL_COMPONENTS = dict(driver_signing=True, staff_signing=True, academy_signing=True, staffing=True)


def prepare_scope(tmp_path, mods, *, semantic_scope="personnel.contract"):
    wake, progress, menus = threading.Event(), [], []
    (tmp_path / "regulations.json").write_text(json.dumps({"categories": [{"key": "native", "options": [{"key": "open"}]}],
        "mod_extensions": {owner: {"schema": 1, "validators": [dict(validator(), scope=semantic_scope)]} for owner in mods}}), encoding="utf-8")
    scope = PackMods(tmp_path, lambda _: None, wake=wake.set, set_loading=progress.append, set_menu=menus.append)
    scope.mods, scope.finished, scope._pack_root, scope.total = dict(mods), True, tmp_path, len(mods)
    for owner, mod in mods.items():
        # Model pack adoption of these already-constructed fixture Stores,
        # including the same cleanup callback used by real source loading.
        mod.sandbox._resources.callback(scope._remove_menu, owner)
    # A staged UI declaration must stay unpublished until native acknowledgement.
    scope.menu.add(next(iter(mods)), "owned", "Owned", "Message")
    runtime = scope.bind_regulations(scopes={semantic_scope})
    scope.poll()
    assert not runtime.ready and progress[-1]["state"] == "loading" and not menus
    return scope, runtime, wake, progress, menus


def pump(scope, wake, future):
    stop = time.monotonic() + 5
    while not future.done() and time.monotonic() < stop:
        wake.wait(.001)
        wake.clear()
        scope.poll()
    return future.result(timeout=.1)


def control_bridge(monkeypatch, control):
    """Reply through the real Bridge waiter; only native control is simulated."""
    session = SimpleNamespace(is_detached=False, create_script=lambda *a, **kw:
                              SimpleNamespace(on=lambda *a: None, is_destroyed=False))
    client = Bridge(None, session, "", lambda *a: None)
    def post(record, timeout):
        status = control(record["operation"])
        client._message({"type": "send", "payload": {"event": "reply", "id": record["id"], "status": status}}, lambda *a: None)
    monkeypatch.setattr(client, "_post", post)
    return client


def test_normal_startup_opens_career_menu_then_activates_one_captured_attempt(source_mod, runner_bundle, tmp_path, monkeypatch):
    """Load the example normally; simulate the game-side bridge replies."""
    example = Path(__file__).resolve().parents[2] / "examples/rookie_contract_mod"
    folder = tmp_path / "data/owned"
    shutil.copytree(example, folder / example.name)
    document = json.loads((example / "regulations.json").read_text(encoding="utf-8"))
    owner = "example.rookie_contracts"
    category = owner + ".length"
    document.update(categories=[{"key": "native", "options": [{"key": "open"}]}], native_extra={"keep": True})
    # Exercise the same fixed loading request for categories and scheduled choices.
    document["mod_extensions"][owner]["scripted_changes"] = [{
        "key": category, "new_regulation": {"key": "five_years"}, "effective_season": 2028}]
    path = folder / "regulations.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    original = path.read_bytes()
    # Existing API mods share the runner and wait for the new rule's readiness.
    source_mod(folder, "ui", mod_id="owned.ui", capabilities=("main-menu",), code=(
        "import wit_world\nclass WitWorld:\n def activate(self): wit_world.add_menu_button('owned','Owned','Message')\n"
        " def on_event(self,n,p): return p\n"))
    progress, menus, operations, channels, reports = [], [], [], [], []
    wake = threading.Event()
    client = control_bridge(monkeypatch, lambda operation: {})
    with ExitStack() as resources:
        worker = resources.enter_context(ThreadPoolExecutor(max_workers=1))
        scope = resources.enter_context(closing(PackMods(tmp_path, reports.append, bridge=client, runner_bundle=runner_bundle,
            wake=wake.set, set_loading=progress.append, set_menu=menus.append)))
        def post(record, timeout):
            operation = record["operation"]
            operations.append(operation)
            def reply(status):
                client._message({"type": "send", "payload": {
                    "event": "reply", "id": record["id"], "status": status}}, lambda *args: None)
            if operation == "regulation-defaults":
                reply({"regulation_defaults": {"pack": str(folder.resolve()),
                    "categories": [{"key": "native", "options": [{"key": "open"}]}]}})
            elif operation == "await-regulations":
                # Career selection must still be blocked while protection arms.
                assert progress[-1]["state"] == "loading" and not menus
                assert record["regulations"] == {"pack": str(folder.resolve()),
                    "categories": scope.regulations.definitions.custom_categories,
                    "scripted_changes": scope.regulations.definitions.custom_scripts}
                reply({"adapters": {"career_preload": {"state": "armed"}}})
            elif operation in ("prepare-regulations", "retire-regulations"):
                def publish():
                    if not channels:
                        channels.append(resources.enter_context(closing(RegulationChannel(scope.regulations.endpoint))))
                    channel = channels[0]
                    if operation == "retire-regulations":
                        channel.reset(channel.epoch + 1)
                        reply({"regulation_control": True})
                    else:
                        channel.publish(custom={category: ["three_years", "five_years"]})
                        reply({"adapters": {"regulations": {"state": "prepared"}}})
                worker.submit(publish)
            elif operation == "restore-native-rules":
                assert scope.regulations.closed
                reply({"regulation_control": True})
            else:
                assert not scope.regulations.ready
                if operation == "install-regulations":
                    assert record["regulations"]["native_rules"] == {
                        "rookie_contract_length_check": False}
                else:
                    assert "native_rules" not in record["regulations"]
                reply({"regulation_control": True, "adapters": {"regulations": {
                    "state": "installed" if operation == "install-regulations" else "active",
                    "career_generation": record["regulations"]["generation"], "components": dict(PERSONNEL_COMPONENTS,
                        rookie_contract_length_check="disabled", staff_prestige_check="native")}}})
        monkeypatch.setattr(client, "_post", post)
        scope.load(dict(state="ready", series="formula", id=folder.name, root=str(folder.resolve())))
        deadline = time.monotonic() + 120
        while scope.loading is not None:
            assert time.monotonic() < deadline, reports
            scope.poll()
            time.sleep(.002)
        scope.poll()  # The next controller tick binds and arms the loaded rules.
        mod = scope.mods[owner]
        assert mod.manifest.api == "0.4.0" and set(scope.mods) == {owner, "owned.ui"}
        assert path.read_bytes() == original
        assert scope._regulation_armed and not scope.regulations.ready and not menus
        assert progress[-1]["state"] == "complete"
        scope.poll()  # Binding and arming happen once, without a status request loop.
        scope.career_preload({"state": "waiting", "generation": 7})
        assert scope.regulations.ready and menus[-1] and progress[-1]["state"] == "complete"
        for generation in (7, 6):
            scope.career_preload({"state": "waiting", "generation": generation})
        assert operations == ["regulation-defaults", "await-regulations", "prepare-regulations", "install-regulations", "activate-regulations"]
        ctx = context()
        ctx["proposal"] = {"native_checks": {"rookie_contract_length_applies": True},
                           "proposed": {"duration_weeks": 208}}
        choice = dict(selection(), rule=category, upcoming=[1, 2028])
        def check():
            return pump(scope, wake, worker.submit(channels[0], [ctx], [choice],
                        {owner: [[0, 0, 0]]}, time.monotonic() + .1))[owner][0]
        assert check() == {"decision": "deny", "message": "Rookie contracts may last at most 3 years."}
        ctx["proposal"]["native_checks"]["rookie_contract_length_applies"] = False
        assert check() == {"decision": "allow"}
        ctx["proposal"]["native_checks"]["rookie_contract_length_applies"] = True
        ctx["effective"]["season"] = choice["evaluation_season"] = 2028
        choice["selected"] = "upcoming"
        assert check() == {"decision": "allow"}  # The upcoming five-year option applies.
        handler = scope.regulations._handler
        runner = mod.runner
        scope.career_preload({"state": "left", "generation": 7})
        assert not scope.regulations.ready and menus[-1] == [] and not runner.closed
        scope.career_preload({"state": "waiting", "generation": 8})
        assert scope.regulations.ready and menus[-1] and channels[0].epoch == 2
        # A quick replacement may overtake the intermediate menu notice.
        scope.career_preload({"state": "waiting", "generation": 9})
        assert scope.regulations.ready and channels[0].epoch == 3
        assert mod.runner is runner and scope.regulations._handler is handler
        mod.close()
        scope.poll()
        scope.poll()
        assert operations[-1] == "restore-native-rules" and operations.count("restore-native-rules") == 1
        assert scope.regulations.closed and scope._regulation_failed
        scope.career_preload({"state": "waiting", "generation": 10})
        assert operations[-1] == "restore-native-rules"


def test_switch_only_policy_requires_complete_installation_and_fresh_career_publication(make_mod, tmp_path, monkeypatch):
    mod = make_mod("", owner="switch.rules")
    switches = {"rookie_contract_length_check": False, "staff_prestige_check": True}
    (tmp_path / "regulations.json").write_text(json.dumps({"mod_extensions": {
        mod.manifest.id: {"schema": 1, "native_rules": switches}}}), encoding="utf-8")
    wake = threading.Event()
    with closing(PackMods(tmp_path, lambda _: None, wake=wake.set)) as scope:
        scope.mods, scope.finished, scope._pack_root = {mod.manifest.id: mod}, True, tmp_path
        runtime = scope.bind_regulations(scopes=set())
        assert runtime._handler is None and not runtime.ready
        with pytest.raises(RuntimeError, match="unavailable"):
            runtime.native_rules()
        with closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            pump(scope, wake, worker.submit(channel.publish))
            values = runtime.native_rules()
            assert values == switches
            values.clear()
            assert runtime.native_rules() == switches
            for report in ({}, {"rookie_contract_length_check": "skipped"},
                           {"rookie_contract_length_check": "disabled", "staff_prestige_check": "disabled"}):
                with pytest.raises(ValueError, match="switch coverage"):
                    runtime.confirm_native(report, pending=True)
                assert not runtime.ready and not mod.sandbox.closed
            report = {"rookie_contract_length_check": "disabled", "staff_prestige_check": "native"}
            runtime.confirm_native(report, pending=True)
            assert not runtime.ready
            runtime.finish_native_activation()
            assert runtime.ready
            pump(scope, wake, worker.submit(channel.reset, 2))
            with pytest.raises(RuntimeError, match="unavailable"):
                runtime.native_rules()
            pump(scope, wake, worker.submit(channel.publish))
            assert runtime.native_rules() == switches and not runtime.ready
            restored = []
            def restore(operation):
                assert operation == "restore-native-rules" and runtime.closed
                restored.append(operation)
                return {"regulation_control": True}
            scope._bridge = control_bridge(monkeypatch, restore)
            runtime.generation = 2
            runtime._bridge = scope._bridge
            mod.close()
            with pytest.raises(RuntimeError, match="unavailable"):
                runtime.native_rules()
            scope.poll()
            scope.poll()
            assert restored == ["restore-native-rules"] and runtime.closed


@pytest.mark.parametrize("cancel_at", ["install-regulations", "activate-regulations"])
def test_cancel_between_native_setup_replies_retires_only_career(make_mod, tmp_path, monkeypatch, cancel_at):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    scope, runtime, wake, _, _ = prepare_scope(tmp_path, {mod.manifest.id: mod})
    with closing(scope), closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
        pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
        operations, resets = [], []
        def control(operation):
            operations.append(operation)
            if operation == "retire-regulations":
                resets.append(worker.submit(channel.reset, 2))
                return {"regulation_control": True}
            if operation == cancel_at:
                return {"regulation_control": "stale"}
            return {"regulation_control": True, "adapters": {"regulations": {
                "state": "installed", "career_generation": 1, "components": PERSONNEL_COMPONENTS}}}
        client = control_bridge(monkeypatch, control)
        # Reset acknowledgement must be serviced on the owning thread, as in
        # Runtime retirement; this test's immediate reply only queues it.
        runtime._activate(client, 1)
        pump(scope, wake, resets[0])
        assert operations[-1] == "retire-regulations" and not runtime.ready
        assert not runtime.closed and not mod.runner.closed and not scope._regulation_failed


def test_startup_needs_no_native_hooks_for_omitted_regulation_sections(make_mod, tmp_path):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    (tmp_path / "regulations.json").write_text('{}', encoding="utf-8")
    client = SimpleNamespace(request=lambda *args: pytest.fail("No rules need native protection"))
    progress, menus = [], []
    with closing(PackMods(tmp_path, lambda _: None, bridge=client,
                          set_loading=progress.append, set_menu=menus.append)) as scope:
        scope.mods, scope.finished, scope._pack_root, scope.total = {mod.manifest.id: mod}, True, tmp_path, 1
        scope._regulation_owners = {mod.manifest.id}
        scope.menu.add(mod.manifest.id, "owned", "Owned", "Message")
        scope.poll()
        assert scope.regulations.ready and not scope._regulation_owners
        assert menus[-1] and progress[-1]["state"] == "complete"


def test_startup_does_not_release_menu_without_confirmed_native_loading_protection(make_mod, tmp_path):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    (tmp_path / "regulations.json").write_text(json.dumps({"categories": [
        {"key": "native", "options": [{"key": "open"}]}], "mod_extensions": {
            mod.manifest.id: {"schema": 1, "validators": [validator()]}}}), encoding="utf-8")
    progress, menus = [], []
    client = SimpleNamespace(regulation_defaults=lambda pack: (),
        request=lambda *args: {"adapters": {"career_preload": {"state": "unavailable"}}})
    with closing(PackMods(tmp_path, lambda _: None, bridge=client,
                          set_loading=progress.append, set_menu=menus.append)) as scope:
        scope.mods, scope.finished, scope._pack_root, scope.total = {mod.manifest.id: mod}, True, tmp_path, 1
        scope._regulation_owners = {mod.manifest.id}
        scope.menu.add(mod.manifest.id, "owned", "Owned", "Message")
        scope._progress("complete")
        with pytest.raises(RuntimeError, match="loading protection was not confirmed"):
            scope.poll()
        assert not scope._regulation_armed and not menus and progress[-1]["state"] == "loading"


def test_native_coverage_requires_the_whole_declared_scope_before_pack_readiness(make_mod, tmp_path):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    scope, runtime, wake, progress, menus = prepare_scope(tmp_path, {mod.manifest.id: mod})
    with closing(scope):
        with pytest.raises(RuntimeError, match="Incomplete regulation binding"):
            runtime.confirm_native(PERSONNEL_COMPONENTS)
        with closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            with pytest.raises(ValueError, match="component report"):
                runtime.confirm_native(None)
            # Signing support is insufficient; truthy integers also cannot
            # substitute for a successful native component installation.
            for staffing in (False, 1):
                with pytest.raises(ValueError, match="coverage: staffing"):
                    runtime.confirm_native(dict(PERSONNEL_COMPONENTS, staffing=staffing))
            assert not runtime.ready and not menus and progress[-1]["state"] == "loading"
            assert not mod.sandbox.closed
            pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
            assert runtime._dispatch.ready and not runtime.ready and not menus
            runtime.confirm_native(PERSONNEL_COMPONENTS, pending=True)
            # Native guards can become enabled just before their activation reply
            # arrives. Coverage already permits checks; UI must still stay held.
            action = worker.submit(channel, [context()], [selection()], {mod.manifest.id: [[0, 0, 0]]}, time.monotonic() + .1)
            assert pump(scope, wake, action) == {mod.manifest.id: [{"decision": "allow"}]}
            assert not runtime.ready and not menus
            runtime.finish_native_activation()
            scope.poll()
            assert runtime.ready and menus[-1] and progress[-1]["state"] == "complete"
            with pytest.raises(ValueError, match="coverage: academy_signing"):
                runtime.confirm_native({"driver_signing": True, "staff_signing": True, "staffing": True})
            scope.poll()
            assert not runtime.ready and menus[-1] == [] and progress[-1]["state"] == "loading"


@pytest.mark.parametrize("rejected", ["unacknowledged", "stale-generation"])
def test_published_data_rejects_unacknowledged_or_stale_actions_before_author_code(make_mod, tmp_path, rejected):
    executed = []
    mod = make_mod("import wit_world\ndef validate(c,r):\n wit_world.report('unexpected')\n return {'decision':'allow'}\n",
                   report=lambda *args: executed.append(args))
    scope, runtime, wake, _, _ = prepare_scope(tmp_path, {mod.manifest.id: mod})
    with closing(scope), closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
        pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
        assert runtime._dispatch.ready and not runtime.ready
        if rejected == "stale-generation":
            runtime.confirm_native(PERSONNEL_COMPONENTS)
            pump(scope, wake, worker.submit(channel.reset, 2))
            pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
            runtime.confirm_native(PERSONNEL_COMPONENTS)
            channel._generations[mod.manifest.id] -= 1
        action = worker.submit(channel, [context()], [selection()], {mod.manifest.id: [[0, 0, 0]]}, time.monotonic() + .1)
        with pytest.raises(HookError, match="unavailable"):
            pump(scope, wake, action)
        assert not executed and mod.sandbox.closed and not runtime.ready


def test_native_preparation_defers_cleanup_ui_until_bridge_reply(make_mod, tmp_path, monkeypatch):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    owner = mod.manifest.id
    scope, runtime, _, progress, menus = prepare_scope(tmp_path, {owner: mod})
    scope._sources = {owner: SimpleNamespace(root=tmp_path)}
    scope._source_runner = mod.runner
    mod.sandbox._resources.callback(scope._remove_sources)
    scope.applied_menu = [{"owner": owner}]
    count = len(progress)
    def control(operation):
        assert runtime.busy
        if operation == "prepare-regulations":
            mod.close()
            assert not menus and len(progress) == count
            return {"adapters": {"regulations": {"state": "unavailable"}}}
        assert operation == "stop-regulations" and runtime.closed
        return {"regulation_control": True}
    with closing(scope):
        with pytest.raises(RuntimeError, match="preparation was not confirmed"):
            runtime._prepare(control_bridge(monkeypatch, control), 1)
        assert not runtime.busy and scope._regulation_failed and runtime.closed
        scope.poll()
        assert menus[-1] == [] and progress[-1]["state"] == "error"


def test_unmapped_native_scope_is_rejected_before_guest_binding(make_mod, tmp_path, monkeypatch):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    def unexpected(*args):
        pytest.fail("Unmapped scope reached guest binding")
    monkeypatch.setattr("launcher.regulations.runtime.SharedHandlerRunner.bind", unexpected)
    with pytest.raises(ValueError, match="No complete native regulation mapping for scope: future.scope"):
        prepare_scope(tmp_path, {mod.manifest.id: mod}, semantic_scope="future.scope")
    assert mod.sandbox.closed


def test_binding_rejects_separate_runners_including_definition_only_owners(make_mod, tmp_path):
    mods = {owner: make_mod("def validate(c,r): return {'decision':'allow'}\n", owner=owner)
            for owner in ("a.rules", "b.data")}
    (tmp_path / "regulations.json").write_text(json.dumps({
        "categories": [{"key": "native", "options": [{"key": "open"}]}],
        "mod_extensions": {"a.rules": {"schema": 1, "validators": [validator()]}, "b.data": {"schema": 1}}}), encoding="utf-8")
    with closing(PackMods(tmp_path, lambda _: None)) as scope:
        scope.mods, scope.finished, scope._pack_root = dict(mods), True, tmp_path
        with pytest.raises(ValueError, match="share one Python runner"):
            scope.bind_regulations(scopes={"personnel.contract"})
        assert all(mod.sandbox.closed for mod in mods.values())
        assert scope._regulation_failed


def test_shared_project_guard_runs_real_mods_once_before_native_writes(make_mods, tmp_path, monkeypatch):
    mods = make_mods({
        "a.rules": "def validate(c,r):\n c['proposal']['budget']=0\n return {'decision':'allow'}\n",
        "b.rules": "def validate(c,r):\n if r['upcoming'] is not None: assert r['upcoming']['parameters']['limit']==99\n if r['current']['key']=='closed': return {'decision':'deny','message':'Rule changed'}\n if c['proposal']['budget']>5: return {'decision':'deny','message':'Budget refused'}\n return {'decision':'allow'}\n"})
    scope, runtime, wake, _, _ = prepare_scope(tmp_path, mods, semantic_scope="development.project")
    module = ModuleType("owned_project_native_entry")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    writes, projections = [], []

    def native(budget):
        writes.append(budget)
        return True

    def project(arguments, result):
        projections.append(arguments["budget"])
        return dict(context(), scope="development.project", proposal={"budget": arguments["budget"]})

    module.start = native
    rules = ModuleType("managers.regulation_manager")
    rules.CALENDAR_KEY = "native"
    exec('''class RegulationManager:
 def _option_key_for_season(self, category_key: 'str', season_no: 'int') -> 'Optional[str]':
  return self.regulations[category_key]['current']
''', vars(rules))
    monkeypatch.setitem(sys.modules, rules.__name__, rules)
    state = SimpleNamespace(season_no=2026, regulation_manager=rules.RegulationManager())
    state.regulation_manager.state = state
    state.regulation_manager.regulation_defs = {"native": {"options": [{"key": "open"}, {"key": "closed"}]}}
    state.regulation_manager.regulations = {"native": {"current": "open", "upcoming": "closed",
        "upcoming_option": {"key": "closed", "limit": 99}}}
    with closing(scope), closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
        policy = NativeRegulationPolicy(state, channel)
        pump(scope, wake, worker.submit(policy.publish))
        channel.validation_ready = False
        assert not runtime.ready and module.start is native
        engine = RegulationGuards(channel, policy.select, "development.project")
        engine.approve(CallableTarget("project", module.__name__, (), "start", str(inspect.signature(native))))
        try:
            for owner in mods:
                engine.register(owner, [CallBinding("project", "project", project, refuse_boolean)])
            def control(operation):
                assert not runtime.ready and scope._regulation_dispatching
                assert module.start(2) is False and not projections and not writes
                assert set(engine.owners.values()) == {"ready"}
                if operation == "activate-regulations":
                    channel.validation_ready = True
                return {"regulation_control": True, "adapters": {"regulations": {
                    "state": "active" if channel.validation_ready else "installed", "career_generation": 1,
                    "components": {"development_projects": engine.groups["project"]["state"] == "active", "staffing": False}}}}
            runtime._activate(control_bridge(monkeypatch, control), 1)

            def start(budget):
                with engine.operation():
                    result = module.start(budget)
                    return result, engine.last_outcome.message

            assert pump(scope, wake, worker.submit(start, 12)) == (False, "Budget refused")
            assert not writes and projections == [12]
            assert pump(scope, wake, worker.submit(start, 4)) == (True, None)
            assert writes == [4] and projections == [12, 4] and runtime.ready
            generation, handlers = runtime._dispatch.generation, runtime._handler
            state.regulation_manager.regulations["native"].pop("upcoming_option")
            state.regulation_manager.regulations["native"].update(current="closed", upcoming=None)
            state.season_no += 1
            assert pump(scope, wake, worker.submit(start, 4)) == (False, "Rule changed")
            assert writes == [4] and set(engine.owners.values()) == {"ready"}
            state.regulation_manager.regulations["native"]["current"] = "open"
            assert pump(scope, wake, worker.submit(start, 4)) == (True, None)
            assert writes == [4, 4] and runtime._dispatch.generation == generation and runtime._handler is handlers
        finally:
            engine.stop()
        assert module.start is native


@pytest.mark.parametrize("failure", ["coverage", "enable"])
def test_failed_native_activation_closes_transport_and_requests_cleanup(make_mod, tmp_path, monkeypatch, failure):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    scope, runtime, wake, progress, menus = prepare_scope(tmp_path, {mod.manifest.id: mod})
    operations = []
    with closing(scope), closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
        pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
        def control(operation):
            operations.append(operation)
            if operation == "stop-regulations":
                assert runtime.closed and not runtime.ready
                return {"regulation_control": True}
            return {"regulation_control": operation == "install-regulations", "adapters": {"regulations": {
                "state": "installed" if operation == "install-regulations" else "unavailable", "career_generation": 1,
                "components": dict(PERSONNEL_COMPONENTS, staffing=failure != "coverage")}}}
        with pytest.raises((ValueError, RuntimeError), match="coverage: staffing|activation was not confirmed"):
            runtime._activate(control_bridge(monkeypatch, control), 1)
        assert operations == (["install-regulations"] + (["activate-regulations"] if failure == "enable" else [])
                              + ["stop-regulations"])
        assert not runtime.ready and not menus and not scope._regulation_dispatching
        scope.poll()
        assert progress[-1]["state"] == "error"


@pytest.mark.parametrize("activation_fails", [False, True])
def test_shared_pack_regulations_bind_once_and_fence_whole_group(tmp_path, source_mod, runner_bundle, activation_fails):
    # Exercise the public manifest path, including definition-only rule owners.
    folder = tmp_path / "data/owned"
    code = "class WitWorld:\n def activate(self): pass\n def on_event(self,n,p): return p\n"
    source_mod(folder, "a.ui", api="0.4.0", capabilities=("main-menu",), code=(
        "import wit_world\n" + code.replace("pass", "wit_world.add_menu_button('open','Open','Message')")))
    for owner in ("b.rules", "c.rules", "d.data"):
        entry = code.replace("pass", "raise ValueError('Regulation owner activation failed')") if activation_fails and owner == "b.rules" else code
        path = source_mod(folder, owner, api="0.4.0", capabilities=("regulations", "diagnostics"), code=entry)
        (path / "src/rules.py").write_text("import wit_world\ndef validate(c,r):\n wit_world.report('validated')\n return {'decision':'allow'}\n")
    (folder / "regulations.json").write_text(json.dumps({"categories": [{"key": "native", "options": [{"key": "open"}]}],
        "mod_extensions": {owner: {"schema": 1, "validators": [validator()] if owner != "d.data" else []}
                           for owner in ("b.rules", "c.rules", "d.data")}}), encoding="utf-8")
    publications, progress, messages, threads = [], [], [], []
    wake = threading.Event()
    def report(line):
        messages.append(line)
        if "CODE MOD REPORT:" in line:
            threads.append(threading.get_ident())
    with closing(PackMods(tmp_path, report, runner_bundle=runner_bundle, wake=wake.set,
                          set_menu=publications.append, set_loading=progress.append)) as scope:
        scope.load(dict(state="ready", series="formula", id="owned", root=str(folder.resolve())))
        stop = time.monotonic() + 120
        while scope.loading is not None:
            assert time.monotonic() < stop, messages
            scope.poll()
            time.sleep(.002)
        assert scope.finished and not publications
        assert scope._regulation_owners == {"b.rules", "c.rules", "d.data"}
        if activation_fails:
            assert not scope.mods and scope.failed == 4 and scope.menu.buttons == []
            assert progress[-1]["state"] == "error"
            with pytest.raises(ValueError, match="Required regulation owner"):
                scope.bind_regulations(scopes={"personnel.contract"})
            return
        mods = dict(scope.mods)
        assert len({id(mod.sandbox) for mod in mods.values()}) == 1
        runtime = scope.bind_regulations(scopes={"personnel.contract"})
        assert set(runtime._handler.validators) == {"b.rules", "c.rules"}
        assert all(mod.snapshot is None for mod in mods.values())
        with closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            runtime.confirm_native(PERSONNEL_COMPONENTS)
            pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
            assert runtime.ready and progress[-1]["state"] == "complete" and len(publications) == 1
            assert runtime.status["owners"]["d.data"] == "bound"
            future = worker.submit(channel, [context()], [selection()], {owner: [[0, 0, 0]] for owner in ("b.rules", "c.rules")},
                                   time.monotonic() + .1)
            assert pump(scope, wake, future) == {owner: [{"decision": "allow"}] for owner in ("b.rules", "c.rules")}
            assert threads == [threading.get_ident()] * 2
            assert [json.loads(line.split(": ", 1)[1])["id"] for line in messages if "CODE MOD REPORT:" in line] == ["b.rules", "c.rules"]
            # A non-regulation peer can close the same interpreter. Required
            # owners remain visible, including the one with no callable table.
            with pytest.raises(SandboxError, match="identifier"):
                mods["a.ui"].event("invalid/event", None)
            scope.poll()
            assert not runtime.ready and not scope.mods and scope.failed == 4
            assert runtime.status["owners"] == dict.fromkeys(("b.rules", "c.rules", "d.data"), "unavailable")
            assert publications[-1] == [] and progress[-1]["state"] == "error"
            future = worker.submit(channel, [context()], [selection()], {"b.rules": [[0, 0, 0]]}, time.monotonic() + .1)
            with pytest.raises(HookError, match="unavailable"):
                pump(scope, wake, future)
            assert channel._unavailable == {"b.rules", "c.rules"}


@pytest.mark.parametrize("fault", ["definitions", "listener"])
def test_preparation_and_transport_startup_failure_keep_error_and_ui_fences(make_mod, tmp_path, monkeypatch, fault):
    progress, menus = [], []
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    data = {"categories": [{"key": "native", "options": [{"key": "open"}]}],
            "mod_extensions": {"owned.rules": {"schema": 1, "validators": [validator()]}}}
    path = tmp_path / "regulations.json"
    path.write_text("{" if fault == "definitions" else json.dumps(data), encoding="utf-8")
    if fault == "listener":
        def unavailable(*args):
            raise OSError("owned listener failure")
        monkeypatch.setattr("launcher.regulations.runtime.RegulationServer", unavailable)
    with closing(PackMods(tmp_path, lambda _: None, set_loading=progress.append, set_menu=menus.append)) as scope:
        scope.mods = {"owned.rules": mod}
        scope.finished, scope._pack_root, scope._regulation_owners = True, tmp_path, {"owned.rules"}
        scope.menu.add("owned.rules", "owned", "Owned", "Message")
        with pytest.raises((ValueError, OSError)):
            scope.bind_regulations(scopes={"personnel.contract"})
        assert progress[-1]["state"] == "error" and not menus
        scope.poll()
        scope._progress()
        scope._progress("complete")
        assert progress[-1]["state"] == "error" and not menus
        # Repairing a live file cannot restart admission or publish staged UI.
        path.write_text(json.dumps(data), encoding="utf-8")
        assert mod.sandbox.closed and mod.snapshot is None
        with pytest.raises(ValueError, match="Required regulation owner"):
            scope.bind_regulations(scopes={"personnel.contract"})
        assert progress[-1]["state"] == "error" and not menus


def test_persistent_dispatch_fences_shared_peers_and_keeps_guests_on_the_controller(make_mods, tmp_path, monkeypatch):
    threads = []
    mods = make_mods({"a.rules": "def validate(c,r): return {'decision':'allow','replacement':1}\n",
                      "b.rules": "import wit_world\ndef validate(c,r):\n wit_world.report('checked')\n return {'decision':'allow'}\n"},
                     report=lambda *_: threads.append(threading.get_ident()))
    scope, runtime, wake, progress, menus = prepare_scope(tmp_path, mods)
    with closing(scope), closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
        assert not runtime.ready  # A transport handshake is not native readiness.
        runtime.confirm_native(PERSONNEL_COMPONENTS)
        assert not runtime.ready
        # Several policy pages of adopted replacements sharing a key.
        # Membership is input-derived; it is not limited to current/upcoming.
        choices = [{"key": "open", "padding": "x" * 1024, "marker": index} for index in range(260)]
        pump(scope, wake, worker.submit(channel.publish, native={"native": choices}))
        assert runtime.ready and progress[-1]["state"] == "complete" and len(menus) == len(menus[0]) == 1
        catalog = runtime._dispatch.catalog
        pump(scope, wake, worker.submit(channel.reset, 2))
        assert not runtime.ready and not catalog.views
        pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
        assert not runtime.ready  # A new career cannot inherit native coverage approval.
        runtime.confirm_native(PERSONNEL_COMPONENTS)
        assert runtime.ready and channel.epoch == 2
        future = worker.submit(channel, [context()], [selection()], {"b.rules": [[0, 0, 0]]}, time.monotonic() + .1)
        assert pump(scope, wake, future) == {"b.rules": [{"decision": "allow"}]}
        assert threads == [threading.get_ident()]
        reply_sent = threading.Event()
        from launcher.regulations import server as transport
        write = transport.regulation_write

        def send_reply(connection, value, deadline):
            write(connection, value, deadline)
            reply_sent.set()

        def publish_after_reply(records):
            # The real UI bridge needs the native caller to leave its validation
            # wait. Waiting here reproduces that dependency without live injection.
            assert reply_sent.wait(timeout=1)
            menus.append(records)

        monkeypatch.setattr(transport, "regulation_write", send_reply)
        scope.set_menu = publish_after_reply
        future = worker.submit(channel, [context()], [selection()], {"a.rules": [[0, 0, 0]]}, time.monotonic() + .1)
        with pytest.raises(HookError, match="unavailable"):
            pump(scope, wake, future)
        assert not channel.closed and not runtime.ready and mods["a.rules"].sandbox.closed
        assert menus[-1] == [] and scope.applied_menu == []
        assert scope.menu.buttons == [] and not scope.mods
        assert runtime.status["owners"] == dict.fromkeys(mods, "unavailable")
        future = worker.submit(channel, [context()], [selection()], {"b.rules": [[0, 0, 0]]}, time.monotonic() + .1)
        with pytest.raises(HookError, match="unavailable"):
            pump(scope, wake, future)
        assert threads == [threading.get_ident()]  # A peer cannot re-enter the failed interpreter.


@pytest.mark.native
def test_owned_cpython_client_dispatches_to_real_wasm_with_one_clock_deadline(make_mods, tmp_path, native_project):
    mods = make_mods(dict.fromkeys(("a.rules", "b.rules", "c.rules"), "def validate(c,r): return {'decision':'allow'}\n"))
    scope, runtime, wake, _, _ = prepare_scope(tmp_path, mods)
    helper = Path(__file__).with_name("fixtures") / "regulation_client_probe.py"
    process = subprocess.Popen([str(native_project.client), "-I", "-S", "-B", str(helper), str(Path(__file__).resolve().parents[2])],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        with closing(scope), ThreadPoolExecutor(max_workers=1) as reader, ExitStack() as cleanup:
            # Unblock readline before the executor joins if an assertion or
            # deadline fails; otherwise the test's own cleanup could hang CI.
            def stop_client():
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            cleanup.callback(stop_client)
            process.stdin.write(json.dumps(runtime.endpoint) + "\n")
            process.stdin.flush()
            assert json.loads(reader.submit(process.stdout.readline).result(timeout=5)) == {"connected": True}
            runtime.confirm_native(PERSONNEL_COMPONENTS)
            tables = {"contexts": [context()], "selections": [selection()],
                      "calls": {owner: [[0, 0, 0]] for owner in mods}}
            process.stdin.write(json.dumps(tables) + "\n")
            process.stdin.flush()
            reply = reader.submit(process.stdout.readline)
            assert json.loads(pump(scope, wake, reply)) == {owner: [{"decision": "allow"}] for owner in mods}
            assert process.wait(timeout=5) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
