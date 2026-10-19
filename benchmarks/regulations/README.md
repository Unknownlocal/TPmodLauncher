# Regulation performance checks

Developer-only acceptance tooling, outside CI and player packaging. Each case
runs in a fresh controller process and an owned native CPython process, using
real Wasm Stores and the ordinary 100 ms action deadline. No game is launched.
The nationality/salary conditions are source-mod functions, not launcher rules.

Build a matching runner once, then reuse it across every case:

```powershell
.venv/Scripts/python.exe -B -m mod_runtime.build --output .state/performance/runner
.venv/Scripts/python.exe -B benchmarks/regulations/run.py --root . --bundle .state/performance/runner --mode resident --native-python libs/python-3.13.16-embed-amd64/python.exe --output .state/performance/current --samples 7
```

For the pre-resident comparison, export commit `85a7c98` to a separate directory,
including `launcher`, `hook_manager`, `mod_runtime`, `notices` and `LICENCE`.
Run that checkout's `mod_runtime.build` from its own directory, using the current
developer interpreter. Point this harness's `--root` and `--bundle` at that
checkout/artifact and select `--mode legacy`. Do not install either artifact into
player libraries. The worker checks every frozen source byte against the chosen
checkout, including line endings; a runner from a different tree is rejected.

`--cases` selects workloads; `report.json` contains only the current invocation.
Use separate output directories for comparisons. Individual reports identify the
component hash, loaded host-source hashes and both Python versions. Source inputs
are trusted benchmark snapshots for unreleased private ABI 0.4.0; public source
admission is unchanged. `fixtures.py` specifies all cardinalities. Large catalog
cases use exact 1 MiB definitions and exact 1 MiB source snapshots. These are
admitted stress fixtures, not a claim to exhaust all possible catalog shapes.

Clean timing runs have no diagnostic monkey patches or tracemalloc. A second,
separate process per case records JSON counts, actual socket send/receive bytes,
guest argument bytes, export fuel and process memory. Stable/context marker
counts count codec calls containing a marker, not the number of embedded records.
Catalog-specific counters distinguish unique base encodings from Store installs.
Warm `action` and `action_after_reload` are separate; control costs are never
included in the warm median. Expected decisions are checked on every action.

Controller/native CPU clocks are process CPU, with the host OS's resolution;
short Windows samples may report zero or a clock quantum. Export times include
diagnostic overhead; use clean wall times for comparisons. Process working set
and lifetime peak include Wasmtime native code, all Stores, interpreter and
allocator retention. They are not exclusive Wasm heap measurements. Host graph
sizes count reachable definition/catalog objects, deduplicated per root. Guest
retained sizes count the active/staged/action graphs with a separate offline
inspection call; its explicit diagnostic allowance never authorizes validation.
Host tracemalloc samples cover definitions/bind and control processing, not native
allocations or clean action timings. Reset probes check release of policy roots;
process RSS need not fall when an allocator retains freed pages.

A valid deadline/resource failure is recorded as `unavailable`, not a passing
throughput result. Unexpected exceptions, runner mismatches and wrong decisions
stop the harness. The outer helper timeout only catches stuck tooling: it never
extends the action deadline or installation allowance. No polling/event integration
is added to the production adapter; the offline memory probe uses its own mod's
existing lifecycle export solely to inspect the owned test Store.

See [the measured report](RESULTS.md) for results and
remaining limits. Raw reports stay under ignored `.state/`.
