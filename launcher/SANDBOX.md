# Sandbox host

`sandbox.py` runs a compiled WebAssembly Component in the external launcher
process. Trusted launcher code supplies its bytes, limits and approved operations.
It accepts no Python source, module path, native-library handle or precompiled
Wasmtime native image. It links no WASI functions or unknown-import stubs.

This is the low-level host. The [author API and manifest](../hook_manager/api/README.md)
are implemented separately in mod_contract.py. pack_mods.py discovers/adopts
components for the Formula pack reported by the game. Normal launcher startup
creates external sandboxes for its admitted code mods. API 0.2.0's approved menu
capability stages declarative buttons, then publishes them through the bounded
Frida channel outside guest execution. General game callbacks remain later work.

## Calls and grants

Each `Sandbox` owns an Engine, Store, Linker, copied operation table and guest
instance. Its store cannot share memory with another instance. Each imported
function must match an explicitly granted `Operation` name and scalar signature
before initialization can execute. The initial profile supports booleans, signed
and unsigned 32/64-bit integers, strings and void results. It rejects resources,
compound values and other import kinds. Non-function exports, including Python's
generated binding metadata, are ignored and never enter the callable table.
An optional required_exports table checks the exact public signatures before
initialization. Parameter names do not grant authority; the types and granted
function name determine the operation.

`Operation.callback` is trusted infrastructure, never mod-authored Python.
Callbacks receive validated plain values; the Wasmtime store is not passed to
them. Callbacks must return bounded values and do bounded, nonblocking work.
Each instance can supply a trusted `cleanup` function to revoke its owned
registrations when it closes or fails. Cleanup cannot undo arbitrary external
side effects. The menu capability owns its declarations/rows and revokes them on
failure or teardown; broader game operations need their own reviewed adapters.

Use an instance on its creating thread. Guest entries across all instances are
serialized by rejecting nested/concurrent entry, rather than queueing it. This
also avoids overlapping Wasmtime Python's process-wide callback exception state.
The host converts callback failures to native Wasmtime errors before returning
through the binding's C callback trampoline.

Wasmtime's compiler uses native worker threads to compile functions in parallel.
This does not enable guest threads or concurrent guest calls. Each instance
still owns its Engine, Store and grants; no compilation cache is enabled.

Trusted callers may pass a `timings` dictionary to collect compilation and
initialization durations in seconds. Failed phases retain their elapsed time.
Measurements finish outside guest execution and do not change fuel/deadline
limits or expose the dictionary to the component.

## Limits and failure

`Sandbox.phase(fuel=..., seconds=...)` lets trusted infrastructure share one fuel
allowance, absolute deadline and host-call count across multiple exports. Work
between calls counts against that deadline; post-return work uses the same fuel.
Phase failures close the sandbox, and subsequent ordinary calls again use their
usual per-call limits. This supports source transfer/import without resetting
the allowance for every chunk. Mods cannot select budgets through their manifest
or enter this host context. Long bounded diagnostics preserve the final trap cause.

Default limits supplied by trusted infrastructure:

| Resource | Bound |
| --- | --- |
| Component binary | 32 MiB |
| Linear memory | 64 MiB, at most one memory per store |
| Guest execution work | 5,000,000 fuel units per initialization/exported call by default |
| Guest deadline | 1 second per initialization/exported call |
| Host operations | 64 per initialization/exported call |
| Import/grant count | 32 |
| Export count | 64 |
| Function parameters | 8 |
| String value | 4,096 UTF-8 bytes |
| Core instances/tables/table elements | 32 / 4 / 10,000 per table |
| Wasm stack | 512 KiB |

Threading, shared memory, memory64, multiple memories within a core module, GC
and typed function references are disabled. The memory/store limits also apply
when instantiation creates memories. Initializers, exported calls and canonical
post-return code share the same execution checks. Budgets reset for the next
successful call; post-return does not receive another budget.

Fuel is Wasmtime's counter for WebAssembly execution work. Most Wasm instructions
cost one unit; some cost zero. A Python component spends fuel on its sandboxed
interpreter as well as the mod's work. Fuel does not count Python statements,
native CPU instructions or seconds, so there is no fixed conversion to runtime.
Running out stops execution with a Wasmtime trap. Trusted host callbacks do not
spend guest fuel; elapsed time and host-call counts have separate limits.
See [Wasmtime's fuel definition](https://docs.rs/wasmtime/49.0.0/wasmtime/struct.Store.html#method.set_fuel).

An epoch timer interrupts guest execution when its deadline expires.
The timer is cancelled and joined before the engine
can be freed. A trap, invalid call/value, host failure or exhausted budget closes
the affected instance, revokes its grants and runs cleanup once. Unrelated
instances remain usable. Calls made from an unsupported thread or during another
entry are rejected before accessing the store.

There are limits to these bounds:

- A guest deadline cannot preempt a blocked trusted Python callback. Late returns
  are rejected; future adapters must keep their host work bounded and nonblocking.
- Compilation validates size-limited portable input but has no hard CPU deadline
  or process-wide memory ceiling. The store's memory limit is for guest linear
  memory, not compiler output or the host's total memory usage.
- Canonical string decoding happens in the binding before the host checks its
  4,096-byte interface limit. Guest memory bounds limit its source data; the
  interface limit is not a promise to avoid every larger temporary allocation.

The Python author profile uses 20,000,000 initialization fuel, a five-second
initialization deadline and 64 core instances. Callbacks retain their 5,000,000
fuel budget and one-second deadline. Limits.initialization_seconds overrides
seconds only during initialization; None reuses seconds. All other bounds stay
the same.
Compilation uses Cranelift's `none` optimization setting to reduce cold-start
cost; code is still compiled and validated by Wasmtime. Larger real mods will
need measurements before changing these approved profiles.
The host is not an OS process sandbox or a guarantee against runtime/host bugs.

Normal startup reads and compiles one mod at a time on a background worker.
`CompiledMod` owns that Engine/Component until the controller transfers them into
a Sandbox or discards them. Compilation creates no Store and executes no guest
code; initialization, activation, later calls and sandbox cleanup retain the
controller's thread checks and shared entry lock. A cancelled session cannot
adopt a late result. Native compilation itself cannot be interrupted, and Python
may wait for a running worker when the launcher process exits.

## Compilation cache

Player startup uses Wasmtime's built-in cache at
`modLauncher/bin/wasmtime-cache/artifacts/`. The generated config sits beside
artifacts, so Wasmtime's cleanup cannot remove the config itself. Wasmtime owns
the cache keys for portable code, compiler configuration and runtime compatibility;
the launcher does not deserialize author-supplied native binaries or maintain a
second cache-key format. Each load still reads the current manifest and portable
component, checks imports/exports, and creates a fresh Store, grants and guest
instance. Readiness, activation and execution budgets remain unchanged.

The cache stays outside data packs and is never distributed. The launcher creates
a private cache directory, checks its owner/access rules, rejects links, hard-linked
or nonregular files, bounds its inspection and generates its own configuration.
Windows compares owner/DACL against Python's private-directory rules; Unix checks
owner/mode and macOS additionally refuses extended ACL entries. It does not widen
permissions or repair an untrusted tree. If these checks or cache setup fail,
compilation proceeds without persistent caching. No guest receives cache paths or
filesystem access. This assumes the launcher and its local cache remain trusted;
it cannot protect against an account able to replace trusted launcher/native code.

`CODE MOD CACHE` reports `enabled` when configuration succeeded, or `disabled`
with a reason for cold fallback. Enabled does not claim a cache hit: Wasmtime's
Python API provides no per-compilation hit counter. Existing compile timings show
the observed cost. Wasmtime manages its own misses and writes after configuration.
The 128 MiB and 4,096-file settings are soft cleanup targets, not hard disk quotas;
metadata and temporary files add space. With the launcher closed, the cache folder
can be removed to force rebuilding; no saves or mod data live there.

See [Wasmtime cache configuration](https://docs.wasmtime.dev/cli-cache.html) and
[Python's private-directory behaviour](https://docs.python.org/3.13/library/os.html#os.mkdir).

## Verification

`tests/launcher/test_sandbox.py` uses real Wasmtime with owned WAT fixtures;
there are no mocked engines, stores, timers or guest calls. The developer
requirements pin Wasmtime for these tests on supported CI platforms. This does
not change the release loader's verified, bundled-only dependency rules.

The embedded-runtime probe also loads this host from a relocated release,
executes a granted operation and verifies cleanup after a guest trap. Run the
normal pytest suite; its existing Windows/portable jobs include these checks.

Upstream references: [Wasmtime security model](https://docs.wasmtime.dev/security.html),
[fuel and epoch interruption](https://docs.wasmtime.dev/examples-interrupting-wasm.html),
[Python API](https://bytecodealliance.github.io/wasmtime-py/).
