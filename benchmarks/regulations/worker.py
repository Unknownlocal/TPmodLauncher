# SPDX-License-Identifier: MPL-2.0
"""One isolated checkout/workload per process; no incompatible-module fallback."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import tracemalloc

from fixtures import inputs, files
from memory import process_memory, retained_size
from meter import Meter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--mode", choices=("legacy", "resident"), required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--native-python", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    from launcher.compilation_cache import CompilationCache
    from launcher.compiled_mod import CompiledMod
    from launcher.regulations import RegulationDefinitions
    from launcher.regulations.runtime import RegulationRuntime
    from mod_runtime import RunnerBundle
    from mod_runtime.bundle import ABI_VERSION, SOURCES
    from mod_runtime.source import SourceManifest, SourceSnapshot
    if args.mode == "legacy":
        # This branch targets only the explicitly exported historical checkout.
        # Current production has no individual Python runner or fallback mode.
        from launcher.source_mod import SourceMod
    else:
        from launcher.shared_source_mod import SharedSourceMod
        from mod_runtime.shared_runtime import SharedPythonRunner
    bundle = RunnerBundle(args.bundle.resolve())
    for name in SOURCES:
        relative = Path(name).relative_to("source")
        origin = args.root / "mod_runtime" / ABI_VERSION / relative if relative == Path("runner.wit") else args.root / "mod_runtime" / relative
        if origin.read_bytes() != bundle._file(name):
            raise ValueError("Bundle differs from selected checkout: " + name)
    case, owners, _, sections, content = inputs(args.case)
    cache_root = args.output.parent / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache = CompilationCache(cache_root)
    report = {"mode": args.mode, "case": args.case, "diagnostics_enabled": args.diagnostics,
              "component_sha256": hashlib.sha256(bundle.component()).hexdigest(), "controller_python": sys.version,
              "file_bytes": len(content), "before_setup_memory": process_memory(), "operations": []}
    wake = threading.Event()
    start, cpu = time.perf_counter_ns(), time.process_time_ns()
    with ExitStack() as stack, Meter(args.diagnostics) as meter:
        if args.mode == "resident":
            from launcher.regulations.validation import catalog
            meter.catalog(catalog)
        mods, snapshots = {}, {}
        for owner in owners:
            source_files = files(case, owner=owner if args.mode == "resident" else None)
            manifest = SourceManifest(owner, "1.0.0", "0.4.0", "mod", frozenset({"regulations"}))
            snapshots[owner] = SourceSnapshot(manifest, source_files, sum(len(text.encode()) for _, text in source_files))
            if args.mode == "legacy":
                prepared = CompiledMod(bundle.component(), cache=cache)
                mods[owner] = stack.enter_context(closing(SourceMod(bundle, snapshots[owner], prepared=prepared)))
                mods[owner].activate()
                meter.bind(mods[owner].sandbox)
        if args.mode == "resident":
            prepared = CompiledMod(bundle.component(), cache=cache)
            group = stack.enter_context(SharedPythonRunner(bundle, tuple(snapshots.values()),
                                                           {owner: {} for owner in owners}, prepared=prepared))
            mods = {owner: SharedSourceMod(group, snapshot) for owner, snapshot in snapshots.items()}
            for mod in mods.values():
                mod.activate()
            meter.bind(group.sandbox)
        if args.diagnostics:
            tracemalloc.start()
        definitions = RegulationDefinitions.from_bytes(content, snapshots, scopes={"personnel.contract"})
        runtime = stack.enter_context(closing(RegulationRuntime(definitions, mods, wake.set)))
        runtime.bind()
        report["bind"] = {"wall_ns": time.perf_counter_ns() - start, "cpu_ns": time.process_time_ns() - cpu,
                          "memory": process_memory(), "definition_bytes": retained_size(definitions)}
        if args.diagnostics:
            report["bind"]["host_traced_bytes"], report["bind"]["host_traced_peak_bytes"] = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        command = [str(args.native_python.resolve()), "-I", "-S", "-B", str(Path(__file__).with_name("client.py")),
                   "--root", str(args.root.resolve()), "--mode", args.mode, "--case", args.case]
        if args.diagnostics:
            command.append("--diagnostics")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8")
        def stop():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        stack.callback(stop)
        with ThreadPoolExecutor(max_workers=1) as reader:
            # Close the process before joining a blocked stdout reader on errors.
            with ExitStack() as reading:
                reading.callback(stop)
                process.stdin.write(json.dumps({"endpoint": runtime.endpoint, "revisions": definitions.revisions}) + "\n")
                process.stdin.flush()
                hello = reader.submit(process.stdout.readline).result(timeout=10)
                if not hello:
                    raise RuntimeError(process.stderr.read())
                report["native"] = json.loads(hello)
                if args.mode == "resident":
                    # This owned transport probe supplies the installation report;
                    # it does not install hooks into a running game.
                    runtime.confirm_native(dict(driver_signing=True, staff_signing=True,
                                                academy_signing=True, staffing=True))
                else:
                    runtime.confirm_native()

                def operation(name):
                    meter.phase = name
                    if args.diagnostics and name not in ("action", "action_after_reload"):
                        tracemalloc.start()
                    before_cpu = time.process_time_ns()
                    process.stdin.write(json.dumps({"operation": name}) + "\n")
                    process.stdin.flush()
                    future = reader.submit(process.stdout.readline)
                    timeout = time.monotonic() + 170
                    while not future.done():
                        if time.monotonic() >= timeout:
                            raise TimeoutError("Benchmark helper did not finish")
                        wake.wait(.0005)
                        wake.clear()
                        runtime.poll()
                    line = future.result()
                    meter.phase = None
                    if not line:
                        raise RuntimeError(process.stderr.read())
                    result = json.loads(line)
                    if args.mode == "resident" and name == "reload" and result["status"] == "pass":
                        runtime.confirm_native(dict(driver_signing=True, staff_signing=True,
                                                    academy_signing=True, staffing=True))
                    if tracemalloc.is_tracing():
                        result["host_allocated_bytes"], result["host_allocated_peak_bytes"] = tracemalloc.get_traced_memory()
                        tracemalloc.stop()
                    result.update(operation=name, controller_cpu_ns=time.process_time_ns() - before_cpu,
                                  ready=runtime.ready, memory=process_memory())
                    report["operations"].append(result)
                    return result["status"] == "pass"

                def inspect_guests(label):
                    # Offline graph inspection has a separate diagnostic
                    # allowance; its result never authorizes a game action.
                    plan = runtime._dispatch.group.active
                    nodes = plan.metric[1] + sum(record.metric[1] for record in plan.records) if plan else 0
                    budget = 8_000_000 + 64_000 * nodes
                    mod = next(iter(mods.values()))
                    with mod.sandbox.phase(fuel=budget, seconds=5):
                        value = mod.event("benchmark.memory", {})
                        value["inspection_fuel"] = budget - mod.sandbox._store.get_fuel()
                        report[label] = value

                if args.mode == "resident":
                    operation("initial")
                    report["host_catalog_bytes"] = retained_size(runtime._dispatch.catalog)
                    if args.diagnostics:
                        inspect_guests("guest_retained")
                for _ in range(args.samples):
                    if not operation("action"):
                        break
                if args.mode == "resident" and runtime.ready:
                    for name in ("reset", "reload", "action_after_reload"):
                        if not operation(name):
                            break
                        if args.diagnostics and name == "reset":
                            report["host_catalog_after_reset_bytes"] = retained_size(runtime._dispatch.catalog)
                            inspect_guests("guest_after_reset")
                report["controller_diagnostics"] = meter.snapshot()
                process.stdin.write('{"operation":"stop"}\n')
                process.stdin.flush()
                if process.wait(timeout=5):
                    raise RuntimeError(process.stderr.read())
    report["after_close_memory"] = process_memory()
    report["owner_failures"] = {owner: mod.sandbox.failure for owner, mod in mods.items() if mod.sandbox.failure}
    report["host_source_sha256"] = {}
    root = args.root.resolve()
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith(("launcher.", "hook_manager.", "mod_runtime.")) and path:
            path = Path(path).resolve()
            if path.is_relative_to(root) and path.suffix == ".py":
                report["host_source_sha256"][path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"mode": args.mode, "case": args.case, "diagnostics": args.diagnostics,
                      "actions_ms": [round(row["wall_ns"] / 1e6, 3) for row in report["operations"] if row["operation"] == "action"],
                      "statuses": [row["status"] for row in report["operations"]]}), flush=True)


if __name__ == "__main__":
    main()
