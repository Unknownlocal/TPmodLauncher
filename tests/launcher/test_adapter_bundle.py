# SPDX-License-Identifier: MPL-2.0
"""Exercise the generated private bridge expression without a native injector."""

import builtins
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from launcher.game_bridge import BridgeError, factory

ROOT = Path(__file__).resolve().parents[2]


def bridge():
    session = SimpleNamespace(create_script=lambda source, **kw: SimpleNamespace(
        source=source, on=lambda *args: None, is_destroyed=False), is_detached=False)
    return factory(ROOT, None)(session, 0, lambda *args: None)


def test_generated_bridge_loads_private_adapters_once_and_cleans_up(monkeypatch):
    client = bridge()
    config = json.JSONDecoder().raw_decode(client.script.source.split("const config = ", 1)[1])[0]
    # Only emulate bootstrap's supported-version check on the portable host.
    # Imports still use the real interpreter, registry and module machinery.
    system = ModuleType("sys")
    system.__dict__.update(vars(sys))
    system.version_info = (3, 13)
    def importing(name, *args, **kwargs):
        return system if name == "sys" else builtins.__import__(name, *args, **kwargs)
    namespace = {"__builtins__": dict(vars(builtins), __import__=importing)}
    started = False
    def pulse(operation, **data):
        nonlocal started
        request = json.dumps(json.dumps({"operation": operation, **data}))
        expression = (config["repeat_prefix"] + request + ")" if started
                      else config["prefix"] + request + config["suffix"])
        result = json.loads(eval(expression, namespace))
        assert result["event"] == "manager", result
        started = True
        return result["status"]

    name = "_tpm_hook_manager_host"
    assert name not in sys.modules
    try:
        assert pulse("status")["adapters"] == {"state": "idle", "modules": 0}
        host = sys.modules[name]
        assert not hasattr(host, "career")  # The loading adapter is the only career owner.
        finders, paths, before = tuple(sys.meta_path), tuple(sys.path), set(sys.modules)
        assert pulse("adapters")["adapters"]["state"] == "loaded"
        prefix = host.adapters.prefix
        snapshot = sys.modules[prefix + ".game_adapters.drivers.snapshot"]
        personnel = sys.modules[prefix + ".game_adapters.regulations.personnel_adapter"]
        channel = sys.modules[prefix + ".game_adapters.regulations.channel"]
        assert snapshot._contract.__globals__ is vars(snapshot)
        assert issubclass(personnel.PersonnelAdapter, host.CallableAdapters)
        assert channel.HookError is host.HookError
        assert host.manager.groups == {} and host.callables.owners == {}
        assert tuple(sys.meta_path) == finders and tuple(sys.path) == paths
        assert not {key for key in set(sys.modules) - before
                    if key == "game_state" or key.startswith(("managers.", "ui."))}
        loaded = dict(host.adapters.modules)
        assert pulse("adapters")["adapters"]["state"] == "loaded"
        assert host.adapters.modules == loaded
        closed = []
        game = ModuleType("game_state")
        game.GameState = type("GameState", (), {})
        monkeypatch.setitem(sys.modules, "game_state", game)
        channel = SimpleNamespace(closed=False, bindings=lambda scope: {}, _rules=set(), _custom=set(),
            close=lambda: closed.append(prefix in sys.modules))
        career = game.GameState()
        ui = ModuleType("ui.screen_manager")
        ui.ScreenManager = type("ScreenManager", (), {
            "handle_event": lambda self, event: "native event", "update": lambda self: "native update"})
        monkeypatch.setitem(sys.modules, ui.__name__, ui)
        screen_manager = ui.ScreenManager()
        screen_manager.current = SimpleNamespace(state=career)
        helper = ModuleType("ui.session_preload")
        helper.advance_session_preload = lambda screen, state: True
        monkeypatch.setitem(sys.modules, helper.__name__, helper)
        for name in ("hub_screen", "load_game_screen", "team_select_screen"):
            module = ModuleType("ui.screens." + name)
            module.advance_session_preload = helper.advance_session_preload
            monkeypatch.setitem(sys.modules, module.__name__, module)
        screen = type("OwnedLoadingScreen", (), {})()
        screen.manager, screen.state = screen_manager, career
        screen._preload_job = type("OwnedPreloadJob", (), {})()
        screen_manager.current = screen
        rejected = pulse("await-regulations", target="author-selected")
        assert rejected["regulation_notices"] and host.adapters.preload is None
        assert pulse("await-regulations")["adapters"]["career_preload"] == {"state": "armed"}
        cached_preload = sys.modules["ui.screens.hub_screen"].advance_session_preload
        assert cached_preload(screen, career) is False
        generation = host.adapters.preload.generation
        assert screen_manager.update() == "native update"  # No general input/update interception.
        published = []
        channel._token = "0" * 64
        channel.publish = lambda **data: published.append(data)
        monkeypatch.setattr(host, "RegulationChannel", lambda endpoint: channel)
        prepared = pulse("prepare-regulations", regulations={"generation": generation,
            "endpoint": {"port": 1, "token": "0" * 64}})
        assert prepared["regulation_notices"] == []
        assert prepared["adapters"]["regulations"] == {"state": "prepared", "components": {}, "career_generation": generation}
        assert published == [{"native": {}, "custom": {}}] and host.adapters.regulations == []
        control = {"generation": generation, "endpoint": {"port": 1, "token": channel._token}}
        rejected = pulse("install-regulations", regulations=dict(control, generation=generation + 1))
        assert rejected["regulation_control"] is False and rejected["regulation_notices"]
        assert host.adapters.channel is channel
        assert pulse("install-regulations", regulations=control)["regulation_control"] is True
        active = pulse("activate-regulations", regulations=control)
        assert active["regulation_control"] is True and active["adapters"]["regulations"]["state"] == "active"
        assert screen_manager.update() == "native update"
        assert cached_preload(screen, career) is True
        guard = host.CallableAdapters()
        host.adapters.regulations.append(guard)
        # The worker behaviour itself is tested with a real thread in the
        # loader suite. Here check the generated bridge's WAIT/retry protocol.
        guard._active = 1
        request = json.dumps(json.dumps({"operation": "shutdown"}))
        assert eval(config["repeat_prefix"] + request + ")", namespace) == "WAIT: native regulation operation is active"
        assert host.adapters.modules == loaded and not guard.closed and closed == []
        guard._active = 0
        assert pulse("stop-regulations", regulations=control)["regulation_control"] is True
        assert host.adapters.preload is None and cached_preload(screen, career) is True
        pulse("await-regulations")
        assert sys.modules["ui.screens.hub_screen"].advance_session_preload(screen, career) is False
        replacement = host.adapters.preload
        assert replacement.generation > generation
        assert pulse("prepare-regulations", regulations=control)["regulation_notices"]
        assert host.adapters.preload is replacement and not replacement.closed
        # Delayed channel-free cleanup still belongs to the previous attempt.
        assert pulse("stop-regulations", regulations=control)["regulation_control"] is False
        assert host.adapters.preload is replacement and not replacement.closed
        assert pulse("stop-regulations", regulations=dict(control, generation=replacement.generation))["regulation_control"] is True
        assert pulse("shutdown")["adapters"] == {"state": "closed", "modules": 0}
        assert guard.closed and closed == [True]
        assert host.adapters.policy is None
        assert not any(key.startswith(prefix) for key in sys.modules)
    finally:
        if name in sys.modules:
            pulse("shutdown")
            del sys.modules[name]


def test_adapter_control_operation_transfers_no_source_or_target_fields(monkeypatch):
    client, sent = bridge(), []
    def post(record, timeout):
        sent.append(record)
        client._message({"type": "send", "payload": {
            "event": "reply", "id": record["id"], "status": {"adapters": {"state": "loaded"}}}}, lambda *args: None)
    monkeypatch.setattr(client, "_post", post)
    assert client.request("adapters")["adapters"]["state"] == "loaded"
    assert sent[0].keys() == {"type", "id", "operation"}
    assert sent[0]["operation"] == "adapters"
    client.request("await-regulations")
    assert sent[-1].keys() == {"type", "id", "operation"} and sent[-1]["operation"] == "await-regulations"
    with pytest.raises(BridgeError, match="no menu declarations"):
        client.request("adapters", buttons=[])


def test_preparation_wait_services_only_controller_transport_and_cancels_on_failure(monkeypatch):
    client, sent, threads, closed = bridge(), [], [], []
    def post(record, timeout):
        sent.append(record)
        if record.get("operation") == "stop-regulations":
            assert closed == [True]
            client._message({"type": "send", "payload": {
                "event": "reply", "id": record["id"], "status": {"regulation_control": True}}}, lambda *args: None)
    monkeypatch.setattr(client, "_post", post)
    def service():
        threads.append(threading.get_ident())
        client._message({"type": "send", "payload": {
            "event": "reply", "id": sent[-1]["id"], "status": {"prepared": True}}}, lambda *args: None)
    runtime = SimpleNamespace(definitions=SimpleNamespace(owner_ids=("owned",)),
        endpoint={"port": 1, "token": "0" * 64}, poll=service, close=lambda: closed.append(True))
    assert client.prepare_regulations(runtime, 1) == {"prepared": True}
    assert threads == [threading.get_ident()] and closed == []
    assert set(sent[0]) == {"type", "id", "operation", "regulations"}
    assert sent[0]["regulations"] == {"generation": 1, "endpoint": runtime.endpoint}
    with pytest.raises(BridgeError, match="bounded regulation setup"):
        client.request("prepare-regulations", regulations={"generation": 1, "endpoint": {}, "target": "arbitrary"}, _service=service)
    def fail():
        raise RuntimeError("Controller service failed")
    runtime.poll = fail
    with pytest.raises(RuntimeError, match="Controller service failed"):
        client.prepare_regulations(runtime, 1)
    assert closed == [True] and client.waiters == {}
    assert sent[-2] == {"type": "manager-cancel", "id": sent[-3]["id"]}
    assert sent[-1]["operation"] == "stop-regulations" and sent[-1]["regulations"] == sent[-3]["regulations"]
