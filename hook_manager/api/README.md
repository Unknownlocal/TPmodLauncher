# Author API 0.1.0

This experimental contract is defined in [mod.wit](mod.wit), using the standard
WebAssembly Component Model's WIT language. Its world is `code-mod`, in package
`modlauncher:api@0.1.0`. It is implemented and tested against real Python guests.

## Mod layout

When discovery is implemented, each direct child with `mod.toml` will declare a
code mod for the data pack selected by the game:

```text
<game-root>/
  modLauncher/                         trusted launcher and manager
  data/<data-mod>/                      selected through the game's interface
    <code-mod>/
      mod.toml
      mod.wasm
      src/mod.py                       readable author source
```

Several code-mod folders can coexist in one selected data pack. This phase reads
only an explicitly supplied folder; it does not scan data packs or activate mods
in the running game. `constants.py` needs no executable additions.

The runnable author template is [examples/diagnostic_mod](../../examples/diagnostic_mod).
Its manifest is plain TOML with the following complete schema:

```toml
schema = 1
id = "example.diagnostics"
version = "0.1.0"
api = "0.1.0"
runtime = "python"
component = "mod.wasm"
capabilities = ["diagnostics"]
```

| Field | Meaning |
| --- | --- |
| `schema` | Integer manifest format, currently 1. |
| `id` | Stable mod identifier: starts with a lowercase letter, followed by lowercase letters, digits, `.`, `_`, `:`, or `-`; at most 128 characters. |
| `version` | SemVer version, including optional prerelease/build identifiers; at most 64 characters. |
| `api` | Exact supported API version, currently `0.1.0`; no automatic version fallback. |
| `runtime` | Approved host profile: `python` or `wasm`. This requests a resource profile, not native Python execution. |
| `component` | Canonical relative `.wasm` path within this folder. Linked or escaping paths are rejected. |
| `capabilities` | Unique requested permissions. Currently only `diagnostics` is implemented. Unknown permissions fail admission. |

All fields are required. Unknown fields are rejected. Manifests are limited to
16 KiB and do not execute code. Imports must also match the manifest's grants;
merely declaring a permission cannot add a function to the manager API.

## Calls

- `report(message: string)` is the only host import. It requires `diagnostics`.
  The host attaches the manifest ID and replaces non-printable characters before
  passing the message to its trusted diagnostic handler. It grants no game access.
- `activate()` is called once after admission. Component initializers may execute
  before it, with the same granted functions and initialization resource limits.
- `on-event(name: string, payload: string) -> string` receives and returns bounded
  JSON plain values. Host infrastructure decides which events to deliver. The
  approved event adapter will validate its particular request/response schema;
  an event name never resolves an arbitrary Python module, function or attribute.

JSON uses the existing manager's value checks: finite numbers, signed 64-bit
integers, plain lists/dictionaries/strings/booleans/null, depth at most 8 and a
1,024-node budget. Each WIT string must also fit 4,096 UTF-8 bytes, including the
serialized payload/response. Native objects, functions and pointers cannot pass.
Invalid responses close the mod and run its owned cleanup.

The host checks exact public function signatures before initialization. Python
components also contain generated private exports; these do not enter the host's
callable table. No private nested function or resource can be invoked through
this API. Package labels alone are not proof of compatibility: manifest version,
imports and public signatures all need to pass.

## Execution budgets

**Fuel is a budget for WebAssembly execution work.** Wasmtime deducts fuel while
the component runs: most Wasm instructions cost one unit, and some cost zero.
For Python mods, this includes the sandboxed Python interpreter's work. It does
not count Python statements or native CPU instructions, and it is not a duration
in seconds. A single Python statement can spend many units. See
[Wasmtime's definition](https://docs.rs/wasmtime/49.0.0/wasmtime/struct.Store.html#method.set_fuel).

The `python` profile allows 20,000,000 fuel units for component initialization.
Each later `activate()` or `on-event()` call receives a fresh 5,000,000 units,
including any guest cleanup needed to return its result. Unused fuel does not
carry over. The `wasm` profile uses 5,000,000 units for initialization too.
The launcher chooses these budgets; a mod cannot raise them in its manifest.

An endless loop keeps spending fuel until Wasmtime stops it. Fuel exhaustion
closes the affected mod, revokes its permissions and runs its owned host cleanup;
other mod instances remain usable.

Calls into trusted launcher handlers do not spend guest fuel. Separate limits
allow one second of elapsed execution time and 64 host calls per initialization
or exported call. The deadline can interrupt guest code, but cannot interrupt a
blocked host handler; a late return is rejected. See the full
[sandbox limits](../../launcher/SANDBOX.md) for memory and other bounds.

## Build Python components

Use full developer Python and a project-local venv. From the launcher checkout:

```console
python -m venv .venv
python -m pip --python .venv install -r requirements-dev.txt
```

Then use that venv's Python (`.venv/Scripts/python.exe` on Windows,
`.venv/bin/python` on Linux/macOS):

```console
python -m launcher.build_mod examples/diagnostic_mod --output examples/diagnostic_mod/mod.wasm
```

Here `python` means the venv interpreter. The helper uses pinned
`componentize-py==0.25.1`, the supplied WIT and generated `wit_world` bindings.
The entry module is `src/mod.py`. It copies up to 1 MiB of Python source into
temporary staging, rejects links/non-Python inputs, uses an empty package search
environment and builds with `--stub-wasi`. It preserves the original source and
refuses to overwrite an existing output. Choose a fresh output when rebuilding.
Builders and their packages are developer tools; players do not install them.

`--stub-wasi` embeds denial stubs for unused WASI operations. Runtime startup
links no host WASI environment, filesystem, network, process or native-library
functions. An attempted operation may raise a Python exception or trap the whole
component. A trap disables that instance; there is no unrestricted Python fallback.

The `python` profile also raises the core-instance limit from 32 to 64 to fit its
interpreter. The test component used about 11.9 million startup fuel units and
about 94,000 for a simple JSON event. These measurements do not establish costs
for every Python mod or a conversion between fuel and Python statements.

Python components include their own sandboxed Python runtime; the current sample
is roughly 18 MiB. Supplied source and a documented build are not proof that an
arbitrary binary was built from them. Sandbox admission enforces host capabilities,
while source/build review enforces the distribution policy on third-party code.
Compiled artifacts are ignored by Git. Retain applicable Python/toolchain notices
when distributing built components; a full distributable release is separate work.

## Current boundaries

The manager does not yet discover folders, transport game events or offer game/UI
operations. No event response is applied to the game by this phase. Additional
capabilities require reviewed adapters and a versioned contract change.
Compilation and blocked trusted callbacks retain the limits documented in the
sandbox host; there is no claim of OS-level isolation or a bug-free runtime.

References: [WIT](https://component-model.bytecodealliance.org/design/wit.html),
[componentize-py](https://github.com/bytecodealliance/componentize-py),
[SemVer](https://semver.org/).
