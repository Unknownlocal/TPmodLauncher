# SPDX-License-Identifier: MPL-2.0
"""Owned handler binding, copied inputs and separately bounded Wasm execution."""

from copy import deepcopy
from contextlib import contextmanager
import threading
import time
from types import SimpleNamespace

import pytest

from launcher.regulations.handler_runner import HandlerRunner
from launcher.sandbox import SandboxError
from mod_runtime._regulation_policy import encode, invocation
from regulation_support import call, context, make_mod, regulation, validator


def test_batches_copy_each_invocation_and_bound_maximum_refusal_replies(make_mod, monkeypatch):
    mod = make_mod("from helpers import validate\ndef mutate(c,r):\n c['proposal']['leaked']=True\n r['current']['option_data']['leaked']=True\n return {'decision':'allow'}\n",
                   extra=(("helpers.py", "def validate(c,r):\n assert 'leaked' not in c['proposal'] and 'leaked' not in r['current']['option_data']\n return {'decision':'deny','message':'\U0001f600'*160}\n"),))
    bound = HandlerRunner.bind(mod.runner, [validator("b"), validator("a", "rules.mutate")])
    ctx, rule = context(), regulation()
    before = deepcopy((ctx, rule))
    timers, timer_class = [], threading.Timer

    def watchdog(*args, **kwargs):
        timer = timer_class(*args, **kwargs)
        timers.append(timer)
        return timer

    monkeypatch.setattr("launcher.sandbox.threading.Timer", watchdog)
    results = bound.evaluate([call("a", ctx, rule)] + [call("b", ctx, rule) for _ in range(31)], time.monotonic() + .1)
    assert results == [{"decision": "allow"}] + [{"decision": "deny", "message": "\U0001f600" * 160}] * 31
    assert (ctx, rule) == before and not mod.sandbox.closed
    assert len(timers) == 1 and not timers[0].is_alive()


def test_maximum_expanded_payload_falls_back_without_losing_the_scalar_limit(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    bound = HandlerRunner.bind(mod.runner, [validator()])
    ctx, rule = context(), regulation()
    ctx["proposal"]["padding"] = ""
    size = len(encode({"context": ctx, "regulation": rule}).encode())
    ctx["proposal"]["padding"] = "x" * (4096 - size)
    assert bound.evaluate([call(ctx=ctx, rule=rule)], time.monotonic() + .1) == [{"decision": "allow"}]
    ctx["proposal"]["padding"] += "x"
    with pytest.raises(ValueError, match="4096"):
        bound.evaluate([call(ctx=ctx, rule=rule)], time.monotonic() + .1)
    assert mod.sandbox.closed


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


@pytest.mark.parametrize("fault", ["depth", "nonfinite"])
def test_expanded_context_limits_apply_before_transport_deduplication(fault):
    ctx = context()
    if fault == "depth":
        ctx["proposal"] = {"a": {"b": {"c": {"d": {"e": {"f": {}}}}}}}
    else:
        ctx["proposal"] = {"money": float("inf")}
    with pytest.raises(ValueError, match="node|depth|finite"):
        invocation(ctx, regulation())


def test_maximum_node_envelopes_prepare_without_consuming_handler_fuel(make_mod):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    bound = HandlerRunner.bind(mod.runner, [validator()])
    ctx, rule = context(), regulation()
    rule["current"]["parameters"]["values"] = []

    def count(value):
        if type(value) is dict:
            return 1 + sum(map(count, value.values()))
        if type(value) is list:
            return 1 + sum(map(count, value))
        return 1

    # Empty objects exercise the container-copy path at the full node limit,
    # which costs substantially more than a list of scalar zeroes in Wasm.
    rule["current"]["parameters"]["values"] = [{} for _ in range(1024 - count({"context": ctx, "regulation": rule}))]
    assert bound.evaluate([call(ctx=ctx, rule=rule)] * 4, time.monotonic() + .1) == [{"decision": "allow"}] * 4
    rule["current"]["parameters"]["values"].append({})
    with pytest.raises(ValueError, match="node"):
        bound.evaluate([call(ctx=ctx, rule=rule)], time.monotonic() + .1)
    assert mod.sandbox.closed


def test_each_handler_keeps_ordinary_fuel_and_still_traps_runaway_code(make_mod):
    mod = make_mod("def validate(c,r):\n if c['proposal'].get('exhaust'):\n  while True: pass\n total=0\n for i in range(1000): total+=i\n return {'decision':'allow'}\n")
    bound = HandlerRunner.bind(mod.runner, [validator()])
    assert bound.evaluate([call()] * 4, time.monotonic() + .1) == [{"decision": "allow"}] * 4
    ctx = context()
    ctx["proposal"]["exhaust"] = True
    with pytest.raises(SandboxError, match="fuel"):
        bound.evaluate([call(ctx=ctx)], time.monotonic() + .1)
    assert mod.sandbox.closed


def test_watchdog_teardown_cannot_return_a_late_approval(monkeypatch):
    clock, closed = [10.0], []

    @contextmanager
    def phase(**_):
        yield
        clock[0] += .2  # Deterministic teardown delay; no sleeps or guest instance.

    def export(name, *args, **kwargs):
        return '{"decision":"allow"}' if name == "validate-regulation" else ""

    runner = SimpleNamespace(sandbox=SimpleNamespace(phase=phase, call=export), close=lambda: closed.append(True))
    bound = HandlerRunner(runner, {"check": (0, validator())})
    monkeypatch.setattr("launcher.regulations.handler_runner.time.monotonic", lambda: clock[0])
    with pytest.raises(SandboxError, match="Late"):
        bound.evaluate([call()], 10.1)
    assert closed == [True]
