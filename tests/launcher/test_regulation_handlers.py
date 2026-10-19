# SPDX-License-Identifier: MPL-2.0
"""Owned handler binding, copied inputs and separately bounded Wasm execution."""

from copy import deepcopy
from contextlib import contextmanager
import threading
from types import SimpleNamespace

import pytest

from launcher.regulations.handler_runner import HandlerRunner
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
    dispatch = ValidationDispatch(model, {mod.manifest.id: HandlerRunner.bind(mod.runner, model.validators[mod.manifest.id])})
    dispatch.publish(native={"native": [{"key": "open"}]})
    return dispatch


def test_resident_slots_copy_each_invocation_and_bound_maximum_refusal_replies(make_mod, monkeypatch):
    mod = make_mod("from helpers import validate\ndef mutate(c,r):\n c['proposal']['leaked']=True\n r['current']['option_data']['leaked']=True\n return {'decision':'allow'}\n",
                   extra=(("helpers.py", "def validate(c,r):\n assert 'leaked' not in c['proposal'] and 'leaked' not in r['current']['option_data']\n return {'decision':'deny','message':'\U0001f600'*160}\n"),))
    bound = session(mod, [validator("b"), validator("a", "rules.mutate")])
    ctx = context()
    before = deepcopy(ctx)
    timers, timer_class = [], threading.Timer

    def watchdog(*args, **kwargs):
        timer = timer_class(*args, **kwargs)
        timers.append(timer)
        return timer

    monkeypatch.setattr("launcher.sandbox.threading.Timer", watchdog)
    results = bound.evaluate([ctx], [selection()], {mod.manifest.id: [["a", 0, 0]] + [["b", 0, 0]] * 31})
    assert results[mod.manifest.id] == [{"decision": "allow"}] + [{"decision": "deny", "message": "\U0001f600" * 160}] * 31
    assert ctx == before and not mod.sandbox.closed
    assert len(timers) == 1 and not timers[0].is_alive()


@pytest.mark.parametrize("fault", ["signature", "foreign_function", "import_budget"])
def test_binding_failure_closes_the_owner_without_publishing_partial_handlers(make_mod, monkeypatch, fault):
    code = {"signature": "async def validate(c,r): return {'decision':'allow'}\n",
            "foreign_function": "from json import loads as validate\n",
            "import_budget": "while True: pass\n"}[fault]
    if fault == "import_budget":
        monkeypatch.setattr("launcher.regulations.handler_runner.SOURCE_FUEL", 1_000_000)
    mod = make_mod(code)
    with pytest.raises(SandboxError, match="synchronous|fuel"):
        HandlerRunner.bind(mod.runner, [validator()])
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
    bound = ValidationRunner(SimpleNamespace(runner=runner))
    bound.active, bound.epoch, bound.generation, bound.required = True, 1, 1, 1
    monkeypatch.setattr("launcher.regulations.validation.runner.time.monotonic", lambda: clock[0])
    with pytest.raises(SandboxError, match="Late"):
        bound.evaluate(("{}", measure({}), 1), 1, 10.1)
    assert closed == [True]
