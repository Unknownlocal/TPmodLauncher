# SPDX-License-Identifier: MPL-2.0
"""Trusted private-wire fixtures, not the future launcher catalog/dispatcher."""

import hashlib
import json

from mod_runtime._regulations.policy import encode
from mod_runtime._regulations.values import entry_fuel, install_fuel, measure
from mod_runtime.runtime import _chunks
from regulation_support import context


def reply(mod, name, *args, fuel=None):
    value = mod.sandbox.call(name, *args, fuel=fuel)
    if value is None:
        return None
    value = json.loads(value)
    assert "error" not in value, (name, value)
    return value


def policy(owner, values=None):
    values = values or [{"points": [25, 18]}, {"points": [30, 20]}, {"points": [35, 25]}]
    records = values + [{"owner": owner}]
    descriptors = []
    for slot, value in enumerate(records):
        metric = measure(value)
        descriptors.append(dict(slot=slot, id=slot + 1, kind="parameters" if slot < len(values) else "option_data",
                                **dict(zip(("bytes", "nodes", "depth", "integer_work"), metric))))
    return {"owner": owner, "revision": hashlib.sha256(owner.encode()).hexdigest(),
            "bindings": [{"rule": 0, "scope": "personnel.contract"}], "rules": ["native"],
            "choices": [[0, "open", i, len(values)] for i in range(len(values))], "records": descriptors,
            "totals": {key: sum(row[key] for row in descriptors) for key in ("bytes", "nodes", "integer_work")}}, records


def install(mod, manifest, records, *, epoch=1, generation=1, reuse=False, commit=True, metadata_only=False):
    if metadata_only:
        with mod.sandbox.phase(fuel=install_fuel(0, 0, 0, 3, 0), seconds=5):
            reply(mod, "begin-regulation-policy", epoch, generation, 0)
            reply(mod, "seal-regulation-policy")
            reply(mod, "commit-regulation-policy", epoch, generation)
        return
    text = encode(manifest)
    chunks = list(_chunks(text.encode()))
    metrics = measure(manifest)
    totals = manifest["totals"]
    fuel = install_fuel(len(text.encode()) + totals["bytes"], metrics[1] + totals["nodes"], len(records),
                        len(chunks) + 5 * len(records) + 5, metrics[3] + totals["integer_work"])
    with mod.sandbox.phase(fuel=fuel, seconds=5):
        reply(mod, "begin-regulation-policy", epoch, generation, len(text.encode()))
        for chunk in chunks:
            reply(mod, "put-regulation-policy", chunk)
        reply(mod, "seal-regulation-policy-manifest")
        if not reuse:
            for slot, value in enumerate(records):
                page = encode([[slot, value]])
                if len(page.encode()) <= 4096:
                    reply(mod, "install-regulation-records", page)
                else:
                    record = encode(value)
                    reply(mod, "begin-regulation-record", slot, len(record.encode()))
                    for chunk in _chunks(record.encode()):
                        reply(mod, "put-regulation-record", chunk)
                    reply(mod, "seal-regulation-record")
        reply(mod, "seal-regulation-policy")
        if commit:
            reply(mod, "commit-regulation-policy", epoch, generation)


def packet(manifest, count=1, ctx=None, *, selected=0):
    return {"owner": manifest["owner"], "revision": manifest["revision"],
            "contexts": [context() if ctx is None else ctx],
            "selections": [{"rule": 0, "evaluation_season": 2026, "selected": "current",
                            "current": [selected, 2026], "upcoming": None}], "calls": [[0, 0, 0]] * count}


def evaluate(mod, packet, action, *, epoch=1, generation=1):
    text = encode(packet)
    chunks = list(_chunks(text.encode()))
    metric = measure(packet)
    budget = entry_fuel(metric[0], metric[1], len(chunks) + 2, metric[3])
    results = []
    with mod.sandbox.phase(fuel=budget, seconds=.1):
        ack = reply(mod, "begin-regulation-action", action, epoch, generation, len(text.encode()), chunks[0])
        if len(chunks) > 1:
            for chunk in chunks[1:]:
                reply(mod, "put-regulation-action", chunk)
            ack = reply(mod, "seal-regulation-action")
        remaining = len(packet["calls"])
        while remaining:
            count = ack["prepared"]
            for slot in range(count):
                result = reply(mod, "validate-resident-regulation", action, slot, fuel=5_000_000)
                assert (result["action"], result["slot"]) == (action, slot)
                results.append(result["result"])
            remaining -= count
            if remaining:
                ack = reply(mod, "prepare-regulation-slots", action, fuel=64_000_000)
    return results
