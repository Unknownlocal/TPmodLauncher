# SPDX-License-Identifier: MPL-2.0
"""Owned handler binding, copied inputs and separately bounded Wasm execution."""

from copy import deepcopy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from launcher.regulations.shared_handler_runner import SharedHandlerRunner
from launcher.sandbox import SandboxError
from mod_runtime._regulations.values import measure
from launcher.regulations import RegulationDefinitions
from launcher.regulations.validation.dispatch import ValidationDispatch
from launcher.regulations.validation.runner import ValidationRunner
import json
from regulation_support import context, make_mod, selection, validator


def session(mod, rows=None):
    data = {"categories": [{"key": "native", "options": [{"key": "open"}]}],
            "mod_extensions": {mod.manifest.id: {"schema": 1, "validators": rows or [validator()]}}}
    model = RegulationDefinitions.from_bytes(json.dumps(data).encode(), {mod.manifest.id: mod.snapshot}, scopes={"personnel.contract"})
    handler = SharedHandlerRunner.bind(mod.runner, model.validators)
    dispatch = ValidationDispatch(model, handler)
    dispatch.publish(native={"native": [{"key": "open"}]})
    return dispatch


def test_custom_rookie_validator_uses_native_applicability_and_adopted_rule_data(make_mod):
    mod = make_mod("""def validate(context, regulation):
 proposal = context['proposal']
 maximum = regulation['current']['parameters']['max_years']
 if (proposal['native_checks']['rookie_contract_length_applies']
     and proposal['proposed']['length_weeks'] > maximum * 52):
  return {'decision':'deny', 'message':'Rookie contract too long'}
 return {'decision':'allow'}
""")
    bound = session(mod)
    bound.publish(native={"native": [{"key": "open", "parameters": {"max_years": 3}}]})
    ctx = context()
    ctx["proposal"] = {"native_checks": {"rookie_contract_length_applies": True},
                       "proposed": {"length_weeks": 208}}
    calls = {mod.manifest.id: [["check", 0, 0]]}
    def decision():
        return bound.evaluate([ctx], [selection()], calls)[mod.manifest.id][0]
    assert decision() == {"decision": "deny", "message": "Rookie contract too long"}
    ctx["proposal"]["native_checks"]["rookie_contract_length_applies"] = False
    assert decision() == {"decision": "allow"}
    ctx["proposal"]["native_checks"]["rookie_contract_length_applies"] = True
    bound.publish(native={"native": [{"key": "open", "parameters": {"max_years": 5}}]})
    assert decision() == {"decision": "allow"}


def test_resident_slots_copy_each_invocation_and_bound_maximum_refusal_replies(make_mod, monkeypatch):
    mod = make_mod("from helpers import validate\ndef mutate(c,r):\n c['proposal']['leaked']=True\n r['current']['option_data']['leaked']=True\n return {'decision':'allow'}\n",
                   extra=(("helpers.py", "def validate(c,r):\n assert 'leaked' not in c['proposal'] and 'leaked' not in r['current']['option_data']\n return {'decision':'deny','message':'\U0001f600'*160}\n"),))
    bound = session(mod, [validator("b"), validator("a", "rules.mutate")])
    ctx = context()
    before = deepcopy(ctx)
    deadlines = []
    watchdog = mod.sandbox._code.watchdog
    arm = watchdog.arm

    def arm_deadline(deadline):
        deadlines.append(deadline)
        return arm(deadline)

    monkeypatch.setattr(watchdog, "arm", arm_deadline)
    results = bound.evaluate([ctx], [selection()], {mod.manifest.id: [["a", 0, 0]] + [["b", 0, 0]] * 31})
    assert results[mod.manifest.id] == [{"decision": "allow"}] + [{"decision": "deny", "message": "\U0001f600" * 160}] * 31
    assert ctx == before and not mod.sandbox.closed
    assert len(deadlines) == 3 and len(set(deadlines)) == 1 and watchdog.token is None


@pytest.mark.parametrize("fault", ["signature", "foreign_function", "import_budget"])
def test_binding_failure_closes_the_owner_without_publishing_partial_handlers(make_mod, monkeypatch, fault):
    code = {"signature": "async def validate(c,r): return {'decision':'allow'}\n",
            "foreign_function": "from json import loads as validate\n",
            "import_budget": "while True: pass\n"}[fault]
    if fault == "import_budget":
        monkeypatch.setattr("launcher.regulations.shared_handler_runner.SOURCE_FUEL", 1_000_000)
    mod = make_mod(code)
    with pytest.raises(SandboxError, match="synchronous|fuel"):
        SharedHandlerRunner.bind(mod.runner, {mod.manifest.id: [validator()]})
    assert mod.sandbox.closed


def test_each_handler_keeps_ordinary_fuel_and_still_traps_runaway_code(make_mod):
    mod = make_mod("def validate(c,r):\n if c['proposal'].get('exhaust'):\n  while True: pass\n total=0\n for i in range(1000): total+=i\n return {'decision':'allow'}\n")
    bound = session(mod)
    assert bound.evaluate([context()], [selection()], {mod.manifest.id: [["check", 0, 0]] * 4})[mod.manifest.id] == [{"decision": "allow"}] * 4
    ctx = context()
    ctx["proposal"]["exhaust"] = True
    with pytest.raises(SandboxError, match="fuel"):
        bound.evaluate([ctx], [selection()], {mod.manifest.id: [["check", 0, 0]]})
    assert mod.sandbox.closed


def test_watchdog_teardown_cannot_return_a_late_approval(monkeypatch):
    clock, closed = [10.0], []

    @contextmanager
    def phase(**_):
        yield
        clock[0] += .2  # Deterministic teardown delay; no sleeps or guest instance.

    def export(name, *args, **kwargs):
        return ('{"action":1,"slot":0,"result":{"decision":"allow"}}' if name == "validate-resident-regulation"
                else '{"action":1,"prepared":1}')

    runner = SimpleNamespace(sandbox=SimpleNamespace(phase=phase, call=export), close=lambda: closed.append(True))
    bound = ValidationRunner(SimpleNamespace(runner=runner), owner="owned.rules")
    bound.active, bound.epoch, bound.generation, bound.required = True, 1, 1, 1
    monkeypatch.setattr("launcher.regulations.validation.runner.time.monotonic", lambda: clock[0])
    with pytest.raises(SandboxError, match="Late"):
        bound.evaluate(("{}", measure({}), 1), 1, 10.1)
    assert closed == [True]
