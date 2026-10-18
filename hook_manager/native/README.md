# Internal native race collector

`profiling.c` implements preallocated counters and measured call stacks.
`../profiling_collector.py` supplies `Capture`, which owns its CPython monitoring
registrations and translates numbered lap/finishing boundaries into native
checkpoints. This is an internal backend; the bridge, release builder and public
mod API do not load or activate it yet.

## Capture lifecycle

The trusted provider supplies unique, verified live function code objects. A
capture enrolls the thread calling `start()`, opens lap 1 and accepts consecutive
lap numbers through `advance_lap(number)`. `finishing()` opens an unnumbered final
window. All control operations run on that enrolled thread; `snapshot()` is
available after `stop()`. New captures use new native objects and rebuild all
monitoring registrations.
Native ownership uses CPython's unique thread-state ID; Python control retains
the owning Thread object, so recycled operating-system IDs cannot adopt a capture.

`pause(True)` checkpoints active calls and suspends their elapsed timing;
`pause(False)` resumes it. Calls observed while paused still contribute invocation
counts with zero paused elapsed time. Lap boundaries also split paused periods.
Stopping truncates open invocations in reporting and lets their actual execution
continue. Target return values and exceptions are untouched.

Coverage is synchronous Python execution on one physical thread. Calls on other
threads are excluded and their selected entry/exit event count is reported.
Generators, coroutines, async generators and attachment inside an already-active
selected call are refused. Task identity, async scheduling, native function
internals and guest execution are unsupported. A future provider must validate
its execution context before advertising coverage.

## Accounting and output

Native rows are sparse tuples `(window_index, function_index, *counters)`;
indices refer to observed windows and the frozen input code tuple. Counter order:

1. `calls_started`
2. `calls_completed`
3. `carried_in`
4. `carried_out`
5. `calls_truncated`
6. `exceptional_exits`
7. `inclusive_ns`
8. `boundary_exclusive_ns`

At a checkpoint, all active measured frames use one clock instant. Open calls
carry forward without another invocation start. Boundary-exclusive time removes
only measured synchronous child intervals; unmeasured descendants remain in it.
Inclusive parent/child or recursive totals must not be added as independent costs.
The clock measures elapsed active time, including blocking, rather than CPU time.

Snapshots include every observed window, even if it has no function rows, with
clock start, wall duration and paused duration. Clock kind/resolution, native
allocated bytes, callback totals, ignored foreign-thread events and explicit
failure reasons and thread-state identity accompany the counters. Python integers preserve uint64 values
internally; the later transport must encode them according to its wire contract.

Counter overflow, exhausted storage and mismatched events stop accumulation
without raising from a native callback. The snapshot is incomplete. Cleanup
failure remains incomplete even after a successful retry, and partial activation
cannot produce a complete snapshot. This private snapshot
materializes the full bounded dataset after stopping; it is not the proposed
paged transport and its Python object memory exceeds the native arena size.

## Storage and monitoring ownership

The internal `../profiling_plan.py` layer connects verified runtime-local
functions to catalogue metadata through opaque binding tokens. One `Bindings`
registry is shared by providers in an interpreter/process generation, with one
`Coordinator` for the race/context. The trusted provider relays tokens through
`Catalogue.bind` and passes fresh `Catalogue.resolve` selections at race entry.
Only data crosses that boundary; code objects and live functions stay local.

Before starting, consumers reserve identical frozen targets, missing coverage,
metrics and binding policy; their summary/full delivery preferences may differ.
Each handle is owner-bound. Releasing one consumer preserves other consumers;
the last release stops owned instrumentation. Cleanup failure keeps the runtime
reservation for retry. Selected rebinding/removal stops the whole synchronous
capture conservatively, reports the old generation and affected window, and
retains partial results until race end. Providers report namespace replacement;
checkpoints additionally detect retained functions whose `__code__` changed.

This layer does not install live race hooks or collect guest internals. Its full
internal snapshot is not a public transport page or summary projection. Admission
and control run on the registry's owning thread; a simulated worker constructs
and operates its registry/coordinator on that worker. No per-call work is added
to the native callbacks. Binding checks scan the selected live references at
control checkpoints; metadata encoding and attribution occur during admission
and post-race export. Existing catalogue state is trusted provider input, not a
public capability to submit arbitrary runtime bindings.

Admission accepts 1..256 unique synchronous code objects. Defaults reserve 64
windows and 128 active measured frames; maxima are 512 windows and 1,024 frames.
The finishing window consumes one reserved window. Each function/window row uses
64 bytes. Storage is touched during preparation to move initial demand-zero page
faults out of measured execution. This does not prevent later OS paging.

Callbacks perform code-identity lookup, checked native arithmetic and local clock
reads. They do no Python object allocation, name lookup, serialization, IPC or
logging. Windows uses QueryPerformanceCounter; the portable C branch uses
CLOCK_MONOTONIC. Windows is the validated game platform for this chunk.

The controller reserves monitoring ID 3 and refuses an occupied ID. Existing
global events, selected-code local events or callbacks for its three event types
are preserved and refused. It uses local PY_START/PY_RETURN and globally enabled,
natively filtered PY_UNWIND. Other tools and `sys.setprofile` remain independent.
See [CPython's monitoring reference](https://docs.python.org/3.13/library/sys.monitoring.html)
for event and tool ownership semantics. Cleanup disables owned events and restores
callbacks before freeing the ID. A cleanup failure retains the lease for retry;
a changed owner is left untouched. The internal coordinator owns one physical
collector; direct independent `Capture` instances still compete for the ID.

## Build and test

From the repository root, using a developer CPython 3.13+ environment with headers,
the pinned developer setuptools and an installed native compiler:

```powershell
& .venv/Scripts/python.exe hook_manager/native/setup.py build_ext --build-lib .cache/race-collector --build-temp .cache/race-collector-objects
$env:MODLAUNCHER_PROFILE_NATIVE = (Resolve-Path .cache/race-collector).Path
& .venv/Scripts/python.exe -m pytest tests/hook_manager/test_profiling_collector.py
```

The extension targets CPython's 3.13 stable ABI. On Windows it imports
`python3.dll`; the target process must resolve a shim matching its interpreter.
The isolated game-DLL probe uses a separate binary directory with the launcher's
3.13 shim. Do not place a different interpreter's shim in a build directory used
by the developer interpreter. Release dependency validation and actual in-game
loading remain integration work.

Portable tests exercise monitoring lifecycle faults without the extension. Native
tests run when `MODLAUNCHER_PROFILE_NATIVE` points to a trusted build; an explicit
bad path fails. Required native CI also fails if the path is absent. The Windows
native acceptance job builds the extension before running those tests. No binary
is checked in or added to the player bundle in this chunk.
