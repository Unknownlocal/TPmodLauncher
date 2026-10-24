# SPDX-License-Identifier: MPL-2.0
"""Dedicated real-Wasm handler dispatch and controller-owned transport lifetime."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import json
import inspect
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.manager import HookError
from hook_manager.callables import CallBinding, CallableTarget, ResultContract
from hook_manager.game_adapters.regulations import RegulationChannel
from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.native_policy import NativeRegulationPolicy
from launcher.pack_mods import PackMods
from launcher.sandbox import SandboxError
from mod_runtime import source
from regulation_support import context, make_mod, selection, validator


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


def test_shared_project_guard_runs_real_mods_once_before_native_writes(make_mod, tmp_path, monkeypatch):
    mods = {
        "a.rules": make_mod("def validate(c,r):\n c['proposal']['budget']=0\n return {'decision':'allow'}\n", owner="a.rules"),
        "b.rules": make_mod("def validate(c,r):\n if c['proposal']['budget']>5: return {'decision':'deny','message':'Budget refused'}\n return {'decision':'allow'}\n", owner="b.rules")}
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
    exec('''class RegulationManager:
 def _option_key_for_season(self, category_key: 'str', season_no: 'int') -> 'Optional[str]':
  return self.regulations[category_key]['current']
''', vars(rules))
    monkeypatch.setitem(sys.modules, rules.__name__, rules)
    state = SimpleNamespace(season_no=2026, regulation_manager=rules.RegulationManager())
    state.regulation_manager.state = state
    state.regulation_manager.regulation_defs = {"native": {"options": [{"key": "open"}]}}
    state.regulation_manager.regulations = {"native": {"current": "open", "upcoming": None}}
    with closing(scope), closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
        policy = NativeRegulationPolicy(state, channel)
        engine = RegulationGuards(channel, policy.select, "development.project")
        engine.approve(CallableTarget("project", module.__name__, (), "start", str(inspect.signature(native))))
        codec = ResultContract({"shape": "bool", "denied": {"literal": False}, "accepted": {"path": [], "one_of": [True]}})
        try:
            for owner in mods:
                engine.register(owner, [CallBinding("project", "project", project, codec)])
            scope.confirm_regulations()
            pump(scope, wake, worker.submit(policy.publish))
            thread = worker.submit(threading.current_thread).result(timeout=1)
            engine.enroll(thread)

            def start(budget):
                with engine.operation():
                    result = module.start(budget)
                    return result, engine.last_refusal

            assert pump(scope, wake, worker.submit(start, 12)) == (False, "Budget refused")
            assert not writes and projections == [12]
            assert pump(scope, wake, worker.submit(start, 4)) == (True, None)
            assert writes == [4] and projections == [12, 4] and runtime.ready
        finally:
            engine.stop()
        assert module.start is native


@pytest.mark.parametrize("activation_fails", [False, True])
def test_shared_pack_regulations_bind_once_and_fence_whole_group(tmp_path, source_mod, runner_bundle, monkeypatch, activation_fails):
    # Enable private fixture admission only for this test; the public parser still
    # rejects 0.4.0 outside this scope. File reads, pack loading and Wasm are real.
    monkeypatch.setattr(source, "SOURCE_CAPABILITIES", {**source.SOURCE_CAPABILITIES,
                                                       "0.4.0": frozenset({"main-menu", "regulations", "diagnostics"})})
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
        assert runtime._handlers["b.rules"] is runtime._handlers["c.rules"]
        assert "d.data" not in runtime._handlers and mods["a.ui"].snapshot is None
        with closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            scope.confirm_regulations()
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
        assert mod.sandbox.closed
        with pytest.raises(ValueError, match="Required regulation owner"):
            scope.bind_regulations(scopes={"personnel.contract"})
        with pytest.raises(RuntimeError):
            scope.confirm_regulations()
        assert progress[-1]["state"] == "error" and not menus


def test_persistent_dispatch_latches_only_the_failed_owner_and_keeps_guests_on_the_controller(make_mod, tmp_path, monkeypatch):
    threads = []
    mods = {"a.rules": make_mod("def validate(c,r): return {'decision':'allow','replacement':1}\n", owner="a.rules"),
            "b.rules": make_mod("import wit_world\ndef validate(c,r):\n wit_world.report('checked')\n return {'decision':'allow'}\n",
                                owner="b.rules", report=lambda *_: threads.append(threading.get_ident()))}
    scope, runtime, wake, progress, menus = prepare_scope(tmp_path, mods)
    with closing(scope), closing(RegulationChannel(runtime.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
        assert not runtime.ready  # A transport handshake is not native readiness.
        scope.confirm_regulations()
        assert not runtime.ready
        # More than one socket frame of adopted replacements sharing a key.
        # Membership is input-derived; it is not limited to current/upcoming.
        choices = [{"key": "open", "padding": "x" * 1024, "marker": index} for index in range(260)]
        pump(scope, wake, worker.submit(channel.publish, native={"native": choices}))
        assert runtime.ready and progress[-1]["state"] == "complete" and len(menus) == len(menus[0]) == 1
        catalog = runtime._dispatch.catalog
        active = catalog.views["b.rules"]
        pump(scope, wake, worker.submit(channel.publish, native={"native": None}))
        assert catalog.views["b.rules"] is active
        pump(scope, wake, worker.submit(channel.reset, 2))
        assert not runtime.ready and not catalog.views
        pump(scope, wake, worker.submit(channel.publish, native={"native": [{"key": "open"}]}))
        assert runtime.ready and channel.epoch == 2
        reply_sent = threading.Event()
        from launcher.regulations import server as transport
        write = transport.regulation_send_action

        def send_reply(connection, value, **kwargs):
            write(connection, value, **kwargs)
            reply_sent.set()

        def publish_after_reply(records):
            # The real UI bridge needs the native caller to leave its validation
            # wait. Waiting here reproduces that dependency without live injection.
            assert reply_sent.wait(timeout=1)
            menus.append(records)

        monkeypatch.setattr(transport, "regulation_send_action", send_reply)
        scope.set_menu = publish_after_reply
        future = worker.submit(channel, [context()], [selection()], {"a.rules": [[0, 0, 0]]}, time.monotonic() + .1)
        with pytest.raises(HookError, match="unavailable"):
            pump(scope, wake, future)
        assert not channel.closed and not runtime.ready and mods["a.rules"].sandbox.closed
        assert menus[-1] == [] and scope.applied_menu == []
        assert scope.menu.buttons == [] and "a.rules" not in scope.mods
        future = worker.submit(channel, [context()], [selection()], {"b.rules": [[0, 0, 0]]}, time.monotonic() + .1)
        assert pump(scope, wake, future) == {"b.rules": [{"decision": "allow"}]}
        assert threads == [threading.get_ident()] and runtime.status["owners"] == {"a.rules": "unavailable", "b.rules": "active"}
        with pytest.raises(HookError, match="unavailable"):
            channel([context()], [selection()], {"a.rules": [[0, 0, 0]]}, time.monotonic() + .1)
        channel._generations["b.rules"] -= 1
        future = worker.submit(channel, [context()], [selection()], {"b.rules": [[0, 0, 0]]}, time.monotonic() + .1)
        with pytest.raises(HookError, match="unavailable"):
            pump(scope, wake, future)
        assert threads == [threading.get_ident()]  # Stale generation never enters author code.


@pytest.mark.native
def test_owned_cpython_client_dispatches_to_real_wasm_with_one_clock_deadline(make_mod, tmp_path, native_project):
    mods = {owner: make_mod("def validate(c,r): return {'decision':'allow'}\n", owner=owner)
            for owner in ("a.rules", "b.rules", "c.rules")}
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
            scope.confirm_regulations()
            contexts = [context() for _ in range(72)]
            for index, ctx in enumerate(contexts):
                ctx["proposal"] = {"padding": "x" * 3450, "identity": index}
            tables = {"contexts": contexts, "selections": [selection()],
                      "calls": {owner: [[0, index, 0] for index in range(offset * 24, (offset + 1) * 24)]
                                for offset, owner in enumerate(mods)}}
            assert len(json.dumps(tables, separators=(",", ":")).encode()) > 256 * 1024
            process.stdin.write(json.dumps(tables) + "\n")
            process.stdin.flush()
            reply = reader.submit(process.stdout.readline)
            assert json.loads(pump(scope, wake, reply)) == {owner: [{"decision": "allow"}] * 24 for owner in mods}
            assert process.wait(timeout=5) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
