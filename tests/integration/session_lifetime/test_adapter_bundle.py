# SPDX-License-Identifier: MPL-2.0
"""Exercise the generated private bridge expression without a native injector."""

import builtins
import json
import os
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from launcher.game_bridge import BridgeError, factory

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def bridge():
    clients = []
    def create():
        session = SimpleNamespace(create_script=lambda source, **kw: SimpleNamespace(
            source=source, on=lambda *args: None, is_destroyed=False), is_detached=False)
        client = factory(ROOT, None)(session, os.getpid(), lambda *args: None)
        clients.append(client)
        return client
    yield create
    for client in clients:
        client.finish_reporting()


def test_foreign_manager_keeps_its_original_ownership_error(monkeypatch):
    from hook_manager.bootstrap import pulse

    foreign = ModuleType("_tpm_hook_manager_host")
    foreign.source_digest, foreign.readiness = "older sources", object()
    monkeypatch.setitem(sys.modules, foreign.__name__, foreign)
    monkeypatch.setattr(sys, "version_info", (3, 13))
    result = json.loads(pulse({}, "owned sources", "owned session", "", '{"operation":"status"}'))
    assert result["event"] == "error" and "different manager" in result["detail"]
    assert "RuntimeError: A different manager" in result["traceback"]
    assert result["activity"] is None and sys.modules[foreign.__name__] is foreign


def test_generated_bridge_loads_private_adapters_once_and_cleans_up(monkeypatch, tmp_path, bridge):
    client = bridge()
    client.journal.create()
    client.reporting.start()
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
        value = eval(expression, namespace)
        deadline = time.monotonic() + 2
        while value.startswith("WAIT:") and time.monotonic() < deadline:
            time.sleep(.001)
            value = eval(expression, namespace)
        result = json.loads(value)
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
        personnel = sys.modules[prefix + ".game_adapters.regulations.regulation_personnel"]
        channel = sys.modules[prefix + ".game_adapters.regulations.channel"]
        assert snapshot._contract.__globals__ is vars(snapshot)
        assert issubclass(personnel.RegulationPersonnelAdapter, host.CallableAdapters)
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
        class GameState:
            def advance_week(self, off_week=False, *, persist=True):
                return True
            def save_season(self, *, manual=False):
                return "owned save"
            def finalize_and_rollover_season(self):
                return None
        game.GameState = GameState
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
        assert json.loads(eval(config["repeat_prefix"] + request + ")", namespace)) == {"event": "waiting", "dependency": "imports"}
        native.__spec__._initializing = False
        defaults = pulse("regulation-defaults")
        assert defaults["regulation_notices"] == []
        assert defaults["regulation_defaults"]["pack"] == str(tmp_path)
        assert defaults["regulation_defaults"]["categories"][0] == native.ENTRY_REGULATIONS[0]
        assert host.adapters.categories is None  # Reading definitions installs no loading hooks.
        regulation_changes = ModuleType("managers.formula_regulation_governance")
        def construct(self, state, saved_bootstrap=None):
            self.state = state
            self.definitions = dict(state.regulation_manager.regulation_defs)
            self.policy = {"mode": "scripted", "scripted_changes": []}
            self.seed, self.staged_sources, self.suppressed_script_ids = "fixed", {}, set()
        def restore(self, data):
            self.policy = data["policy"]
        def save(self):
            return {"policy": self.policy, "definition_snapshot": self.definitions}
        def stage(self, changes, *, source=None, decision_id=None, effective_season=None):
            for key, option in changes.items():
                self.state.regulation_manager.pending_rules[key] = option
                self.state.regulation_manager.regulations[key]["upcoming"] = option
                self.staged_sources[key] = {"source": source, "id": decision_id, "effective_season": effective_season}
        def send(self, subject, body, actions=None, *, structured_content=None):
            self.state.email_manager.inbox.append({"body": body, "structured_content": structured_content})
        regulation_changes.FormulaRegulationGovernanceManager = type("RegulationChanges", (), {
            "__init__": construct, "load_state": restore, "save_state": save, "stage_changes": stage,
            "process_week": lambda self: "native week", "_trigger_candidates": lambda self, snapshot: [], "_send": send})
        regulation_changes.option_neighbours = lambda spec, current_key: [row["key"] for row in spec["options"]]
        regulation_changes.normalize_policy = lambda raw, definitions: json.loads(json.dumps(raw))
        monkeypatch.setitem(sys.modules, regulation_changes.__name__, regulation_changes)
        pulse("changes")
        inactive = regulation_changes.FormulaRegulationGovernanceManager(SimpleNamespace(
            regulation_manager=SimpleNamespace(regulation_defs={})))
        progress = {"schema": 1, "categories": {}}
        inactive.load_state({"policy": inactive.policy, "mod_automatic_changes": progress})
        assert inactive.save_state()["mod_automatic_changes"] == progress
        assert host.adapters.categories is None and host.adapters.channel is None
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
        categories = [{"key": "owned.rule", "default": "open", "options": [{"key": "open"}, {"key": "limited"}]}]
        scripts = [{"id": "owned:change", "key": "owned.rule", "new_regulation": {"key": "open"}, "effective_season": 2}]
        assert pulse("await-regulations", regulations={"pack": str(tmp_path), "categories": categories,
            "scripted_changes": scripts, "automatic_changes": {"owned.rule": {
                "mode": "automatic", "base_chance": 1, "announcement_week": 5}}})["adapters"]["career_preload"] == {"state": "armed"}
        loader = host.adapters.categories
        # Screen compatibility can fail much later than pack setup. It must
        # report through the journal without waiting for another status reply.
        catalogue = ModuleType("ui.screens.regulations_screen")
        catalogue.RegulationsScreen = type("RegulationsScreen", (), {
            "_draw_sections": lambda self, surface, left_rect, changed: 40,
            "_draw_regulation_card": lambda self, surface, entry, rect: None})
        original_sections = catalogue.RegulationsScreen._draw_sections
        monkeypatch.setitem(sys.modules, catalogue.__name__, catalogue)
        late = pulse("changes")
        assert host.activity.writer.wait_idle(2)
        delivered = client.drain_activity()
        warnings = [record for record in delivered if "UI COMPATIBILITY FAILED" in record.get("detail", "")]
        assert len(warnings) == 1 and warnings[0]["level"] == "WARNING"
        assert not any(record.get("level") == "ERROR" and record.get("detail") == host.adapters.regulation_screen.error_record["detail"]
                       for record in delivered)
        assert late["adapters"]["career_preload"] == {"state": "armed"}
        assert catalogue.RegulationsScreen._draw_sections is original_sections
        assert pulse("status").get("regulation_notices", []) == []
        career.regulation_manager = native.RegulationManager()
        career.regulation_manager.state = career
        career.regulation_manager.regulation_defs = career.regulation_manager._load_regulation_defs()
        assert career.regulation_manager.regulation_defs["owned.rule"] == categories[0]
        career.season_no, career.week_index = 3, 5
        career.regulation_manager.regulations = {"owned.rule": {"current": "open", "upcoming": None}}
        career.regulation_manager.pending_rules = {}
        career.email_manager = SimpleNamespace(inbox=[], send_email=lambda *args, **kwargs: None)
        career.formula_regulation_governance = regulation_changes.FormulaRegulationGovernanceManager(career)
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
        assert host.adapters.coverage["automatic_changes"] is True
        assert career.formula_regulation_governance.process_week() == "native week"
        assert not career.regulation_manager.pending_rules
        active = pulse("activate-regulations", regulations=control)
        assert active["regulation_control"] is True and active["adapters"]["regulations"]["state"] == "active"
        assert screen_manager.update() == "native update"
        assert cached_preload(screen, career) is True
        career.formula_regulation_governance.process_week()
        assert career.regulation_manager.pending_rules == {"owned.rule": "limited"}
        assert len(career.email_manager.inbox) == 1
        assert pulse("restore-native-rules", regulations=control)["regulation_control"] is True
        assert host.adapters.channel is channel and not channel.validation_ready
        guard = host.CallableAdapters()
        host.adapters.regulations.append(guard)
        # The worker behaviour itself is tested with a real thread in the
        # loader suite. Here check the generated bridge's WAIT/retry protocol.
        guard._active = 1
        request = json.dumps(json.dumps({"operation": "shutdown"}))
        assert json.loads(eval(config["repeat_prefix"] + request + ")", namespace)) == {"event": "waiting", "dependency": "operations"}
        assert host.adapters.modules == loaded and not guard.closed and closed == []
        guard._active = 0
        # Retirement uses the same WAIT/idle boundary but retains the loading
        # owner and authenticated connection for another career.
        resets = []
        channel.epoch = 1
        channel.reset = resets.append
        guard._active = 1
        request = json.dumps(json.dumps({"operation": "retire-regulations", "regulations": control}))
        assert json.loads(eval(config["repeat_prefix"] + request + ")", namespace)) == {"event": "waiting", "dependency": "operations"}
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
        with monkeypatch.context() as idle:
            def unexpected_collection(*args, **kwargs):
                raise AssertionError("A status reply collected native state")
            for owner, method in ((host.discovery, "poll"), (host.readiness, "snapshot"),
                                  (host.menu, "poll"), (host.themes, "poll"), (host.settings, "poll")):
                idle.setattr(owner, method, unexpected_collection)
            assert pulse("status")["state"] == "ready"
        assert pulse("shutdown")["adapters"] == {"state": "closed", "modules": 0}
        assert host.adapters.regulation_screen is None
        assert guard.closed and closed == [True]
        assert host.adapters.policy is None
        assert loader.closed and host.adapters.categories is None
        assert loader.regulation_changes.closed
        assert not any(key.startswith(prefix) for key in sys.modules)
    finally:
        if name in sys.modules:
            pulse("shutdown")
            del sys.modules[name]
        client.drain_activity()
        client.journal.remove()


def test_adapter_control_operation_transfers_no_source_or_target_fields(monkeypatch, bridge):
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


def test_preparation_wait_services_only_controller_transport_and_cancels_on_failure(monkeypatch, bridge):
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


def test_bridge_services_regulations_only_when_woken(monkeypatch, bridge):
    client, sent, calls, failures = bridge(), [], [], []
    initial = threading.Event()
    monkeypatch.setattr(client, "_post", lambda record, timeout: sent.append(record))
    def service():
        calls.append(threading.get_ident())
        initial.set()
        if len(calls) == 2:
            client._message({"type": "send", "payload": {"event": "reply", "id": sent[0]["id"],
                            "status": {"prepared": True}}}, lambda _: None)
    def native_work():
        try:
            assert initial.wait(2)
            time.sleep(.04)
            assert len(calls) == 1, "Regulation service ran without an event"
        except BaseException as error:
            failures.append(error)
        finally:
            client.wake_service()
    worker = threading.Thread(target=native_work)
    worker.start()
    try:
        result = client.request("prepare-regulations", regulations={"generation": 1,
            "endpoint": {"port": 1, "token": "0" * 64}}, _service=service)
        assert result == {"prepared": True} and calls == [threading.get_ident()] * 2
    finally:
        worker.join(2)
    assert not failures and not worker.is_alive(), failures
