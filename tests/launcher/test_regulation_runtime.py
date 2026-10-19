# SPDX-License-Identifier: MPL-2.0
"""Dedicated real-Wasm handler dispatch and controller-owned transport lifetime."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, nullcontext
import json
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from hook_manager.manager import HookError
from hook_manager.regulations import RegulationChannel
from launcher.pack_mods import PackMods
from mod_runtime.source import SourceManifest
from regulation_support import call, make_mod, validator


def prepare_scope(tmp_path, mods):
    wake, progress, menus = threading.Event(), [], []
    (tmp_path / "regulations.json").write_text(json.dumps({"categories": [{"key": "native", "options": [{"key": "open"}]}],
        "mod_extensions": {owner: {"schema": 1, "validators": [validator()]} for owner in mods}}), encoding="utf-8")
    scope = PackMods(tmp_path, lambda _: None, wake=wake.set, set_loading=progress.append, set_menu=menus.append)
    scope.mods, scope.finished, scope._pack_root, scope.total = dict(mods), True, tmp_path, len(mods)
    for owner, mod in mods.items():
        # Model pack adoption of these already-constructed fixture Stores,
        # including the same cleanup callback used by real source loading.
        mod.sandbox._resources.callback(scope._remove_menu, owner)
    # A staged UI declaration must stay unpublished until native acknowledgement.
    scope.menu.add(next(iter(mods)), "owned", "Owned", "Message")
    runtime = scope.bind_regulations(scopes={"personnel.contract"})
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


@pytest.mark.parametrize("activation_fails", [False, True])
def test_later_regulation_owner_fences_earlier_ui_until_binding_or_rejection(tmp_path, monkeypatch, activation_fails):
    manifests = {owner: SourceManifest(owner, "1.0.0", "0.4.0", "mod", frozenset({cap}))
                 for owner, cap in (("a.ui", "main-menu"), ("b.rules", "regulations"))}
    publications = []
    with closing(PackMods(tmp_path, lambda _: None, set_menu=publications.append)) as scope:
        monkeypatch.setattr(scope, "_folder", lambda _: tmp_path)
        monkeypatch.setattr(scope, "_candidates", lambda _: [tmp_path / owner for owner in manifests])
        monkeypatch.setattr(scope, "_manifest", lambda path: (path, manifests[path.name]))

        def preparation(executor, bundle, root, **kwargs):
            result = SimpleNamespace(snapshot=manifests[root.name], compiled=None, timings={}, close=lambda: None)
            return nullcontext(SimpleNamespace(take=lambda: result))

        def source_mod(bundle, manifest, **options):
            def activate():
                if manifest.id == "a.ui":
                    options["menu"](manifest.id, "open", "Open", "Message")
                    assert not publications  # A later owner is already reserved.
                elif activation_fails:
                    raise ValueError("Regulation owner activation failed")
            return SimpleNamespace(activate=activate, close=options["cleanup"])

        # Only control source construction: the real pack activation/publication
        # order is exercised without guest builds or public API 0.4 admission.
        monkeypatch.setattr("launcher.pack_mods.SourcePreparation", preparation)
        monkeypatch.setattr("launcher.pack_mods.SourceMod", source_mod)
        scope.load({"id": "owned"})
        assert scope.finished
        assert scope._regulation_owners == (set() if activation_fails else {"b.rules"})
        assert publications == ([scope.menu.buttons] if activation_fails else [])


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
        with pytest.raises(ValueError, match="one preparation"):
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
        assert runtime.ready and progress[-1]["state"] == "complete" and len(menus) == len(menus[0]) == 1
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
        future = worker.submit(channel, "a.rules", [call()], time.monotonic() + .1)
        with pytest.raises(HookError, match="unavailable"):
            pump(scope, wake, future)
        assert not channel.closed and not runtime.ready and mods["a.rules"].sandbox.closed
        assert menus[-1] == [] and scope.applied_menu == []
        assert scope.menu.buttons == [] and "a.rules" not in scope.mods
        future = worker.submit(channel, "b.rules", [call()], time.monotonic() + .1)
        assert pump(scope, wake, future) == [{"decision": "allow"}]
        assert threads == [threading.get_ident()] and runtime.status["owners"] == {"a.rules": "unavailable", "b.rules": "bound"}
        with pytest.raises(HookError, match="unavailable"):
            channel("a.rules", [call()], time.monotonic() + .1)


@pytest.mark.native
def test_owned_cpython_client_dispatches_to_real_wasm_with_one_clock_deadline(make_mod, tmp_path, native_project):
    scope, runtime, wake, _, _ = prepare_scope(tmp_path, {"owned.rules": make_mod("def validate(c,r): return {'decision':'allow'}\n")})
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
            process.stdin.write(json.dumps([call()]) + "\n")
            process.stdin.flush()
            reply = reader.submit(process.stdout.readline)
            assert json.loads(pump(scope, wake, reply)) == [{"decision": "allow"}]
            assert process.wait(timeout=5) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
