# Sandbox host

`sandbox.py` runs a compiled WebAssembly Component in the external launcher
process. Trusted launcher code supplies its bytes, limits and approved operations.
It accepts no Python source, module path, native-library handle or precompiled
Wasmtime native image. It links no WASI functions or unknown-import stubs.

This is an internal interface. The author-facing WIT world, manifest, discovery,
game transport and menu proof are later steps. Normal launcher startup does not
create a sandbox or load code mods yet.

## Calls and grants

Each `Sandbox` owns an Engine, Store, Linker, copied operation table and guest
instance. Its store cannot share memory with another instance. Each imported
function must match an explicitly granted `Operation` name and scalar signature
before initialization can execute. The initial profile supports booleans, signed
and unsigned 32/64-bit integers, strings and void results. It rejects resources,
compound values and other import/export kinds. Parameter names do not grant any
authority; the types and granted function name determine the operation.

`Operation.callback` is trusted infrastructure, never mod-authored Python.
Callbacks receive validated plain values; the Wasmtime store is not passed to
them. Callbacks must return bounded values and do bounded, nonblocking work.
Each instance can supply a trusted `cleanup` function to revoke its owned
registrations when it closes or fails. Cleanup cannot undo arbitrary external
side effects; no game operations are exposed by this chunk.

Use an instance on its creating thread. Guest entries across all instances are
serialized by rejecting nested/concurrent entry, rather than queueing it. This
also avoids overlapping Wasmtime Python's process-wide callback exception state.
The host converts callback failures to native Wasmtime errors before returning
through the binding's C callback trampoline.

## Limits and failure

Default limits supplied by trusted infrastructure:

| Resource | Bound |
| --- | --- |
| Component binary | 32 MiB |
| Linear memory | 64 MiB, at most one memory per store |
| Instructions | 5,000,000 fuel units per initialization/exported call |
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

Fuel bounds guest instruction work; an epoch timer interrupts guest execution
when its deadline expires. The timer is cancelled and joined before the engine
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

These limits need reviewed profiles when adding real Python components. The
scalar test components do not establish a working Python-guest author contract.
The host is not an OS process sandbox or a guarantee against runtime/host bugs.

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
