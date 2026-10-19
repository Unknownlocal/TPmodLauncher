# SPDX-License-Identifier: MPL-2.0
"""Owned native client process; all input construction precedes timed operations."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import inputs, tables
from meter import Meter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("legacy", "resident"), required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    from hook_manager.manager import HookError
    from hook_manager.regulations import RegulationChannel
    config = json.loads(sys.stdin.readline())
    case, owners, native, sections, _ = inputs(args.case)
    ctx, expanded, bindings = tables(case, owners, native, sections, config["revisions"])
    ctx["proposal"]["context_token"] = "0000"

    def nodes(value):
        children = value.values() if type(value) is dict else value if type(value) is list else ()
        return 1 + sum(nodes(child) for child in children)

    if case.get("context") == "nodes":
        ctx["proposal"]["values"] = []
        remaining = 1024 - nodes({"context": ctx, "regulation": expanded[owners[0]][0]})
        ctx["proposal"]["values"] = [{} for _ in range(remaining)]
    elif case.get("context") == "bytes":
        ctx["proposal"]["padding"] = ""
        remaining = 4096 - len(json.dumps({"context": ctx, "regulation": expanded[owners[0]][0]}, ensure_ascii=False, separators=(",", ":")).encode())
        ctx["proposal"]["padding"] = "x" * remaining
    elif case.get("context") == "integer":
        ctx["proposal"]["value"] = 10**3000
    count = case["calls"] * len(owners) if case.get("context") else 1
    contexts = [deepcopy(ctx) for _ in range(count)]
    for index, value in enumerate(contexts):
        # Fixed-width identities keep an exact byte-boundary fixture exact.
        value["proposal"]["context_token"] = f"{index:04}"
    selections, selection_slots, calls, legacy = [], {}, {}, {}
    for owner_index, owner in enumerate(owners):
        calls[owner], legacy[owner] = [], []
        for index, rule in enumerate(expanded[owner]):
            if rule["key"] not in selection_slots:
                selection_slots[rule["key"]] = len(selections)
                selections.append({"rule": rule["key"], "evaluation_season": 2026, "selected": "current",
                                   "current": [0, 2026], "upcoming": None})
            context_slot = owner_index * case["calls"] + index if case.get("context") else 0
            calls[owner].append([bindings[owner][index], context_slot, selection_slots[rule["key"]]])
            legacy[owner].append({"validator": f"v{bindings[owner][index]:02}", "context": contexts[context_slot], "regulation": rule})
    subscribed = {row["rule"] for section in sections.values() for row in section["validators"]}
    native = {rule: options for rule, options in native.items() if rule in subscribed}
    custom = {row["key"]: [row["options"][0]["key"]] for section in sections.values() for row in section.get("categories", [])}
    channel = RegulationChannel(config["endpoint"])
    try:
        print(json.dumps({"connected": True, "python": sys.version, "contexts": len(contexts), "calls": sum(map(len, calls.values()))}), flush=True)
        with Meter(args.diagnostics) as meter:
            for line in sys.stdin:
                operation = json.loads(line)["operation"]
                if operation == "stop":
                    break
                start, cpu = time.perf_counter_ns(), time.process_time_ns()
                meter.phase = operation
                try:
                    if operation in ("action", "action_after_reload"):
                        deadline = time.monotonic() + .1
                        if args.mode == "legacy":
                            result = {owner: channel(owner, legacy[owner], deadline) for owner in owners}
                        else:
                            result = channel(contexts, selections, calls, deadline)
                        expected = {"decision": "deny", "message": "Salary exceeds the permitted amount"} if args.case == "salary" else {"decision": "allow"}
                        if result != {owner: [expected] * case["calls"] for owner in owners}:
                            raise ValueError("Benchmark decision mismatch")
                    elif operation == "reset":
                        channel.reset(channel.epoch + 1)
                    elif operation in ("initial", "changed", "metadata", "reload"):
                        if operation == "changed":
                            for options in native.values():
                                for option in options:
                                    option["parameters"]["value"] += 1
                        channel.publish(native={rule: None for rule in native} if operation == "metadata" else native,
                                        custom={rule: None for rule in custom} if operation == "metadata" else custom)
                    else:
                        raise ValueError("Unknown benchmark operation")
                    response = {"status": "pass"}
                except (HookError, OSError) as error:
                    response = {"status": "unavailable", "error": type(error).__name__ + ": " + str(error)}
                finally:
                    meter.phase = None
                response.update(wall_ns=time.perf_counter_ns() - start, cpu_ns=time.process_time_ns() - cpu,
                                cpu_start_ns=cpu, cpu_end_ns=time.process_time_ns(), diagnostics=meter.snapshot())
                print(json.dumps(response), flush=True)
    finally:
        channel.close()


if __name__ == "__main__":
    main()
