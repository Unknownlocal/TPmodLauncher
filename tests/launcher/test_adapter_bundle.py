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


def test_generated_bridge_loads_private_adapters_once_and_cleans_up(monkeypatch, tmp_path):
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
        assert host.manager.groups == {}
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
        career.data_dir = tmp_path
        native = ModuleType("managers.regulation_manager")
        native.RegulationManager = type("RegulationManager", (), {"_load_regulation_defs": lambda self: {"native": {}}})
        native.RegulationManager._load_regulation_defs.__annotations__ = {"return": "Dict[str, Dict[str, Any]]"}
        monkeypatch.setitem(sys.modules, native.__name__, native)
        native.ENTRY_REGULATIONS = [{"key": "native", "options": [{"key": "open"}]}]
        native.CAR_PROFILE_REGULATION, native.CALENDAR_REGULATION = {"key": "profile"}, {"key": "calendar"}
        native.FORMULA_FUEL_REGULATIONS = native.BONUS_REGULATIONS = []
        class TyreModel:
            @classmethod
            def load(cls, path: "str") -> "'TyreModel'":
                return None
        native.TyreModel = TyreModel
        native.regulation_for_model = lambda model: {"key": "compound"}
        host.discovery.snapshot = {"state": "ready", "series": "formula", "root": str(tmp_path)}
        native.__spec__ = SimpleNamespace(_initializing=True)
        request = json.dumps(json.dumps({"operation": "regulation-defaults"}))
        assert eval(config["repeat_prefix"] + request + ")", namespace) == "WAIT: native regulation definitions are loading"
        native.__spec__._initializing = False
        defaults = pulse("regulation-defaults")
        assert defaults["regulation_notices"] == []
        assert defaults["regulation_defaults"]["pack"] == str(tmp_path)
        assert defaults["regulation_defaults"]["categories"][0] == native.ENTRY_REGULATIONS[0]
        assert host.adapters.categories is None  # Reading definitions installs no loading hooks.
        governance = ModuleType("managers.formula_regulation_governance")
        def construct(self, state, saved_bootstrap=None):
            self.state = state
            self.definitions = dict(state.regulation_manager.regulation_defs)
            self.policy = {"mode": "scripted", "scripted_changes": []}
        def restore(self, data):
            self.policy = data["policy"]
        governance.FormulaRegulationGovernanceManager = type("Governance", (), {"__init__": construct, "load_state": restore})
        governance.normalize_policy = lambda raw, definitions: json.loads(json.dumps(raw))
        monkeypatch.setitem(sys.modules, governance.__name__, governance)
        ui = ModuleType("ui.screen_manager")
        ui.ScreenManager = type("ScreenManager", (), {
            "handle_event": lambda self, event: "native event", "update": lambda self: "native update",
            "switch_to": lambda self, screen: setattr(self, "current", screen)})
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
        categories = [{"key": "owned.rule", "default": "open", "options": [{"key": "open"}]}]
        scripts = [{"id": "owned:change", "key": "owned.rule", "new_regulation": {"key": "open"}, "effective_season": 2}]
        assert pulse("await-regulations", regulations={"pack": str(tmp_path), "categories": categories,
            "scripted_changes": scripts})["adapters"]["career_preload"] == {"state": "armed"}
        loader = host.adapters.categories
        career.regulation_manager = native.RegulationManager()
        career.regulation_manager.state = career
        career.regulation_manager.regulation_defs = career.regulation_manager._load_regulation_defs()
        assert career.regulation_manager.regulation_defs["owned.rule"] == categories[0]
        career.formula_regulation_governance = governance.FormulaRegulationGovernanceManager(career)
        career.formula_regulation_governance.load_state({"policy": {"mode": "scripted", "scripted_changes": []}})
        assert career.formula_regulation_governance.policy["scripted_changes"] == scripts
        cached_preload = sys.modules["ui.screens.hub_screen"].advance_session_preload
        assert cached_preload(screen, career) is False
        generation = host.adapters.preload.generation
        assert screen_manager.update() == "native update"  # No general input/update interception.
        published = []
        channel._token = "0" * 64
        channel.publish = lambda **data: published.append(data)
        monkeypatch.setattr(host.adapter_code.game_adapters.regulations.channel, "RegulationChannel", lambda endpoint: channel)
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
        assert pulse("restore-native-rules", regulations=control)["regulation_control"] is True
        assert host.adapters.channel is channel and not channel.validation_ready
        guard = host.CallableAdapters()
        host.adapters.regulations.append(guard)
        # The worker behaviour itself is tested with a real thread in the
        # loader suite. Here check the generated bridge's WAIT/retry protocol.
        guard._active = 1
        request = json.dumps(json.dumps({"operation": "shutdown"}))
        assert eval(config["repeat_prefix"] + request + ")", namespace) == "WAIT: native regulation operation is active"
        assert host.adapters.modules == loaded and not guard.closed and closed == []
        guard._active = 0
        # Retirement uses the same WAIT/idle boundary but retains the loading
        # owner and authenticated connection for another career.
        resets = []
        channel.epoch = 1
        channel.reset = resets.append
        guard._active = 1
        request = json.dumps(json.dumps({"operation": "retire-regulations", "regulations": control}))
        assert eval(config["repeat_prefix"] + request + ")", namespace) == "WAIT: native regulation operation is active"
        assert not channel.validation_ready and not guard.closed and not resets
        guard._active = 0
        owner = host.adapters.preload
        assert pulse("retire-regulations", regulations=control)["regulation_control"] is True
        assert host.adapters.preload is owner and not owner.closed and host.adapters.channel is channel
        assert guard.closed and resets == [2] and host.adapters.policy is None
        assert pulse("retire-regulations", regulations=control)["regulation_control"] is True and resets == [2]
        # The retained connection publishes again without another channel or
        # preloader construction. A delayed stop cannot remove a newer policy.
        screen._preload_job = type("OwnedPreloadJob", (), {})()
        assert cached_preload(screen, career) is False
        next_control = dict(control, generation=owner.generation)
        assert pulse("prepare-regulations", regulations=next_control)["adapters"]["regulations"]["state"] == "prepared"
        assert pulse("retire-regulations", regulations=control)["regulation_control"] is False
        control = next_control
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
        assert loader.closed and host.adapters.categories is None
        assert loader.scripts.closed
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
            "event": "reply", "id": record["id"], "status": {"adapters": {"state": "loaded"},
                "regulation_defaults": {"pack": str(ROOT), "categories": [{"key": "native"}]}}}}, lambda *args: None)
    monkeypatch.setattr(client, "_post", post)
    assert client.request("adapters")["adapters"]["state"] == "loaded"
    assert sent[0].keys() == {"type", "id", "operation"}
    assert sent[0]["operation"] == "adapters"
    assert client.regulation_defaults(ROOT) == [{"key": "native"}]
    assert set(sent[-1]) == {"type", "id", "operation"} and sent[-1]["operation"] == "regulation-defaults"
    with pytest.raises(BridgeError, match="selected pack"):
        client.regulation_defaults(ROOT / "other-pack")
    with pytest.raises(BridgeError, match="no regulation setup"):
        client.request("regulation-defaults", regulations={"target": "author-selected"})
    client.request("await-regulations")
    assert sent[-1].keys() == {"type", "id", "operation"} and sent[-1]["operation"] == "await-regulations"
    client.request("await-regulations", regulations={"pack": str(ROOT), "categories": [{"key": "owned.rule"}]})
    assert sent[-1]["regulations"]["categories"] == [{"key": "owned.rule"}]
    with pytest.raises(BridgeError, match="bounded prepared"):
        client.request("await-regulations", regulations={"pack": str(ROOT), "categories": [], "target": "author-selected"})
    with pytest.raises(BridgeError, match="bounded prepared"):
        client.request("await-regulations", regulations={"pack": str(ROOT), "categories": [{"key": "owned.rule"}],
            "scripted_changes": {"code": "author-selected"}})
    control = {"generation": 1, "endpoint": {"port": 1, "token": "0" * 64}}
    client.request("install-regulations", regulations=dict(control, native_rules={"staff_prestige_check": False}))
    assert sent[-1]["regulations"]["native_rules"] == {"staff_prestige_check": False}
    for values in ({"arbitrary.target": False}, {"staff_prestige_check": 0}):
        with pytest.raises(BridgeError, match="booleans"):
            client.request("install-regulations", regulations=dict(control, native_rules=values))
    with pytest.raises(BridgeError, match="bounded regulation setup"):
        client.request("activate-regulations", regulations=dict(control, native_rules={}))
    with pytest.raises(BridgeError, match="no menu declarations"):
        client.request("adapters", buttons=[])


def test_preparation_wait_services_only_controller_transport_and_cancels_on_failure(monkeypatch):
    client, sent, threads = bridge(), [], []
    def post(record, timeout):
        sent.append(record)
    monkeypatch.setattr(client, "_post", post)
    def service():
        threads.append(threading.get_ident())
        client._message({"type": "send", "payload": {
            "event": "reply", "id": sent[-1]["id"], "status": {"prepared": True}}}, lambda *args: None)
    control = {"generation": 1, "endpoint": {"port": 1, "token": "0" * 64}}
    assert client.request("prepare-regulations", regulations=control, _service=service) == {"prepared": True}
    assert threads == [threading.get_ident()]
    assert set(sent[0]) == {"type", "id", "operation", "regulations"} and sent[0]["regulations"] == control
    with pytest.raises(BridgeError, match="bounded regulation setup"):
        client.request("prepare-regulations", regulations={"generation": 1, "endpoint": {}, "target": "arbitrary"}, _service=service)
    def fail():
        raise RuntimeError("Controller service failed")
    with pytest.raises(RuntimeError, match="Controller service failed"):
        client.request("prepare-regulations", regulations=control, _service=fail)
    assert client.waiters == {} and sent[-1] == {"type": "manager-cancel", "id": sent[-2]["id"]}
