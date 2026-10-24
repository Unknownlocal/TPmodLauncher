# Regulation performance tooling

This developer tooling runs owned workloads outside CI and player packaging.
Each case uses a fresh external launcher process, a native CPython helper and
real Wasm execution under the ordinary 100 ms action deadline. No game is launched.
Nationality and salary conditions are mod functions; the launcher does not own
those rules.

| File | Responsibility |
| --- | --- |
| `run.py` | Run selected cases in separate timing and diagnostic processes |
| `worker.py` | Load the selected checkout and exact matching runner, then own execution and cleanup |
| `client.py` | Build proposals and exchange policy/actions through the native channel |
| `fixtures.py` | Define source, rule data, proposals and expected decisions |
| `meter.py` | Optionally count JSON, socket, guest-call, fuel and memory work |
| `memory.py` | Read process memory and count retained Python objects |

The current resident mode puts all source owners in one shared runner and binds
regulations through `RegulationRuntime`. Instrumentation attaches once to the
physical Store. Guest inspection uses each owner's lifecycle event to inspect
its retained regulation view; views can share records, so their byte counts
must not be added as independent allocations.

Legacy comparison explicitly loads an exported older checkout and its matching
runner. It does not supply a production fallback. Every frozen source byte must
match the selected bundle; an incompatible checkout stops the run.

Clean timing has no diagnostic patches or tracemalloc. Diagnostic runs record
JSON counts, socket bytes, guest arguments, fuel and process memory separately.
Reports include the runner hash, loaded source hashes and Python versions.
Warm actions are separated from policy publication, changes, reset and reload.
Unexpected errors or wrong decisions stop the case. Resource/deadline failures
are recorded as unavailable rather than successful throughput.

Process working set and peak include Wasmtime, its Stores and interpreters, and
allocator retention. They are not exclusive Wasm heap measurements. Host object
counts remove duplicate references within each measured root; guest inspection
has a separate diagnostic allowance that cannot authorise gameplay. Resetting a
policy releases its objects but need not reduce memory retained by an allocator.

[Recorded results](RESULTS.md) describe their measured implementation and limits;
they do not establish acceptance for the current shared runtime. Raw reports stay
under ignored `.state/`.
