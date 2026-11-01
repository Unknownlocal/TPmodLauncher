# SPDX-License-Identifier: MPL-2.0
"""Check regulation diagnostics and source-runner lifetime through the controller."""

import json
from pathlib import Path
import threading
import time

from launcher import process_lifecycle as lifecycle
from launcher.pack_mods import PackMods
from tests.controller_device import rig


def test_validation_error_keeps_severity_in_file_and_console(rig, tmp_path, capsys):
    from hook_manager.game_adapters.regulations.guards import RegulationGuards

    device, controller, _, _ = rig
    notices = []
    channel = lambda *a: {}
    channel.bindings = lambda scope: {}
    channel.validation_ready = True
    channel._unavailable = set()
    guard = RegulationGuards(channel, lambda *a: {}, "personnel.contract", submit_log=notices.append)
    # An owned but unadmitted worker produces the actual native diagnostic.
    thread = threading.Thread(target=lambda: guard._decision(lambda: {}, set()))
    thread.start()
    thread.join(2)
    assert not thread.is_alive()
    guard.stop()
    payload = json.loads(json.dumps({"event": "compatibility", **notices[0]}))
    path = tmp_path / "launcher.log"
    with lifecycle.reporter(path) as logger:
        controller.submit_log = lifecycle.bind_reporter(logger, component="controller")
        controller.start()
        try:
            session = controller.sessions[10]
            controller.events.put(("bridge", (10, session, {"type": "send", "payload": payload})))
            controller.poll()
            assert controller.sessions[10] is session and not controller.stopping
        finally:
            controller.close()
    for output in (path.read_text(), capsys.readouterr().err):
        assert " ERROR modLauncher.regulations " in output
        assert "pid=10" in output and "REGULATIONS VALIDATION FAILED" in output
        assert "Unscoped worker or reentrant regulation validation" in output


def test_source_mods_follow_controller_session_identity(rig, source_mod, runner_bundle):
    device, controller, messages, _ = rig
    game = Path(controller.game).parent
    for name in ("first", "second"):
        source_mod(game / "data" / name, f"owned.{name}")
    controller.mods_factory = lambda report, bridge: PackMods(game, report, runner_bundle=runner_bundle)
    def send(pid, session, name):
        pack = dict(state="ready", series="formula", id=name, root=str((game / "data" / name).resolve()))
        controller.events.put(("bridge", (pid, session, {"type": "send", "payload": {"event": "pack", "pack": pack}})))
    def drain(scope):
        deadline = time.monotonic() + 30
        while scope.loading is not None:
            assert time.monotonic() < deadline
            controller.poll(timeout=max(0, deadline - time.monotonic()))
    controller.start()
    try:
        first = controller.sessions[10]
        send(10, first, "first")
        controller.poll()
        initial = controller.pack_mods[10]
        drain(initial)
        old_mod = initial.mods["owned.first"]
        send(10, first, "second")
        controller.poll()
        assert list(initial.mods) == ["owned.first"]
        first.is_detached = True
        device.child(10, controller.game)
        controller.poll()
        assert initial.closed and old_mod.sandbox.closed
        replacement = controller.sessions[10]
        send(10, first, "first")  # A stale source pack cannot reattach to the new session.
        send(10, replacement, "second")
        controller.poll()
        current_scope = controller.pack_mods[10]
        drain(current_scope)
        assert list(current_scope.mods) == ["owned.second"]
        current_mod = current_scope.mods["owned.second"]
    finally:
        controller.close()
    assert current_scope.closed and current_mod.sandbox.closed
    assert not controller.pack_mods and not controller.cleanup_failed
    assert sum("CODE MOD ACTIVE:" in line for line in messages) == 2


def test_controller_cancels_source_job_on_exit_and_keeps_child_handling_responsive(rig, source_mod, runner_bundle, monkeypatch):
    from mod_runtime import preparation
    device, controller, messages, _ = rig
    game = Path(controller.game).parent
    mod = source_mod(game / "data/owned pack", "owned.pending")
    entered, release = threading.Event(), threading.Event()
    results = []
    original = preparation._prepare_group
    def blocked(*args):
        result = original(*args)
        results.append(result)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(preparation, "_prepare_group", blocked)
    controller.mods_factory = lambda report, bridge: PackMods(game, report, runner_bundle=runner_bundle)
    controller.start()
    scope = None
    try:
        first = controller.sessions[10]
        pack = dict(state="ready", series="formula", id=mod.parent.name, root=str(mod.parent.resolve()))
        controller.events.put(("bridge", (10, first, {"type": "send", "payload": {"event": "pack", "pack": pack}})))
        controller.poll()
        scope = controller.pack_mods[10]
        assert entered.wait(30)
        device.child(20, controller.game)
        controller.poll()
        assert 20 in controller.sessions and ("resume", 20) in device.calls
        first.is_detached = True
        first.handlers["detached"]("process-terminated")
        controller.poll()
        assert scope.closed and 10 not in controller.pack_mods and results[0].snapshots is not None
    finally:
        release.set()
        controller.close()
        if scope is not None:
            scope.executor.shutdown(wait=True)
    assert results[0].snapshots is None and results[0].component is None and results[0].compiled is None
    assert not any("CODE MOD ACTIVE:" in line or "CODE MOD REPORT:" in line for line in messages)
    assert not controller.cleanup_failed
