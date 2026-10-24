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
        assert host.career.status() == {"state": "pending", "generation": 0}
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
        monkeypatch.setattr(host.career, "current", lambda generation: career)
        coverage = host.adapters.install_current_regulations(1, channel, native_rules={}, report=pytest.fail)
        assert host.adapters.policy is not None
        assert coverage == {"driver_signing": False, "staff_signing": False, "academy_signing": False,
            "development_projects": False, "rookie_contract_length_check": "native",
            "staff_prestige_check": "native", "staffing": False}
        assert host.adapters.stop_regulations()
        closed.clear()
        published = []
        channel._token = "0" * 64
        channel.publish = lambda **data: published.append(data)
        monkeypatch.setattr(host, "RegulationChannel", lambda endpoint: channel)
        prepared = pulse("prepare-regulations", regulations={"generation": 1,
            "endpoint": {"port": 1, "token": "0" * 64}})
        assert prepared["regulation_notices"] == []
        assert prepared["adapters"]["regulations"] == {"state": "prepared", "components": {}, "career_generation": 1}
        assert published == [{"native": {}, "custom": {}}] and host.adapters.regulations == []
        control = {"generation": 1, "endpoint": {"port": 1, "token": channel._token}}
        rejected = pulse("install-regulations", regulations=dict(control, generation=2))
        assert rejected["regulation_control"] is False and rejected["regulation_notices"]
        assert host.adapters.channel is channel
        assert pulse("install-regulations", regulations=control)["regulation_control"] is True
        active = pulse("activate-regulations", regulations=control)
        assert active["regulation_control"] is True and active["adapters"]["regulations"]["state"] == "active"
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
        assert pulse("shutdown")["adapters"] == {"state": "closed", "modules": 0}
        assert guard.closed and closed == [True]
        assert host.adapters.policy is None
        assert host.career.status()["state"] == "stopped"
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
    with pytest.raises(BridgeError, match="no menu declarations"):
        client.request("adapters", buttons=[])


def test_preparation_wait_services_only_controller_transport_and_cancels_on_failure(monkeypatch):
    client, sent, threads, closed = bridge(), [], [], []
    monkeypatch.setattr(client, "_post", lambda record, timeout: sent.append(record))
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
    assert sent[-1] == {"type": "manager-cancel", "id": sent[-2]["id"]}
