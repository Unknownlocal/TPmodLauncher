# Author API 0.2.0

This experimental contract is defined in [mod.wit](0.2.0/mod.wit), using the standard
WebAssembly Component Model's WIT language. Its world is `code-mod`, in package
`modlauncher:api@0.2.0`. Existing API 0.1.0 diagnostics components remain supported
through the retained [0.1.0 contract](0.1.0/mod.wit). The manifest selects its exact
version; there is no automatic fallback. Both run as real sandboxed components.
Every supported contract lives at `hook_manager/api/<version>/mod.wit`. The
builder selects that path from the validated manifest's `api` field and passes
requested capabilities as feature switches, without version-specific branches.

The host's supported versions, capability lists and lifecycle signatures are
defined in [launcher/mod_api.py](../../launcher/mod_api.py). Manifest validation
and binding those permissions to trusted handlers live in
[launcher/mod_contract.py](../../launcher/mod_contract.py).

## Mod layout

Each direct child with `mod.toml` declares a code mod for the Formula data pack
selected by the game:

```text
<game-root>/
  modLauncher/                         trusted launcher and manager
  data/<data-mod>/                      selected through the game's interface
    <code-mod>/
      mod.toml
      mod.wasm
      src/mod.py                       readable author source
```

Several code-mod folders can coexist in one selected data pack. The external
launcher discovers and activates their components after the game's startup pack
is known. `constants.py` needs no executable additions. Mod code runs outside the
game. The `main-menu` capability adds native rows with click confirmations;
general game-event delivery and gameplay operations are not implemented yet.

Runnable templates are [diagnostics](../../examples/diagnostic_mod), retained on
API 0.1.0, and the [native UI example](../../examples/menu_mod), using API 0.2.0.
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
| `api` | Exact supported API version: `0.1.0` or `0.2.0`. Menu registration requires `0.2.0`. |
| `runtime` | Approved host profile: `python` or `wasm`. This requests a resource profile, not native Python execution. |
| `component` | Canonical relative `.wasm` path within this folder. Linked or escaping paths are rejected. |
| `capabilities` | Unique requested permissions: `diagnostics`, plus `main-menu` in API 0.2.0. Unknown permissions fail admission. |

All fields are required. Unknown fields are rejected. Manifests are limited to
16 KiB and do not execute code. Imports must also match the manifest's grants;
merely declaring a permission cannot add a function to the manager API.

## Discovery and lifetime

- Only ordinary direct child directories containing `mod.toml` are candidates.
  The pack root itself and deeper nested folders are not code-mod entries.
  Linked directories and junctions are skipped; linked pack paths, manifest files
  and component paths are rejected. Manifests and components must be regular files.
- Valid manifests are collected before any component initializes. Every valid
  manifest sharing a duplicate mod ID is rejected; no copy wins by folder order.
  Remaining mods activate in ascending ID order. There is no dependency ordering
  or mod-to-mod communication in this version.
- Discovery allows at most 4,096 direct pack entries and 32 candidate mod folders.
  Exceeding either limit rejects code loading for that process before activation.
  These are launcher limits, not manifest options.
- A malformed manifest, missing/invalid component, or initialization/activation
  failure is logged without stopping valid neighbours or the game. An ordinary
  data pack with no code mods is supported and loads zero components.
- The startup pack is fixed for that process. Repeated bridge records do not
  reload code or change its selected pack. Source edits do not trigger rebuilding
  or live reloading; no compiler or package installer runs at player startup.
- Each game session owns separate sandbox instances. Process exit, replacement,
  PID reuse and launcher shutdown close them in reverse activation order. A new
  game process discovers its own selected pack and receives fresh mod state.

The launcher log records `CODE MOD ACTIVE`, approved `CODE MOD REPORT` messages,
rejections, the active count and `CODE MOD CLOSED`. IDs and messages are quoted
as JSON, and each record is labelled with its game process ID. These are the
confirmation of mod loading. `CODE MOD MENU` reports native UI readiness separately;
activation can finish while the adapter waits for the game's normal menu import.

`CODE MOD TIMING` records each attempted mod's `phase_seconds`, labelled with
its pack and mod ID. The phases are:

| Phase | Work measured |
| --- | --- |
| `manifest` | Read and validate the manifest and its parent folder. |
| `component_read` | Read the bounded portable Wasm file. |
| `compile` | Validate and compile the component using Wasmtime's parallel compiler. |
| `initialize` | Instantiate the component and run its start functions. |
| `activate` | Run the mod's activation export. |
| `registration` | Send staged menu registrations, for mods requesting menu access. |

Only reached phases are recorded; a failed phase retains its duration. Invalid
manifests and duplicate IDs include their manifest timing in the rejection
record. `CODE MOD READY` includes discovery timing and `total_seconds` for the
whole pack-loading attempt; discovery failures include these in their rejection
record instead. Totals also include validation, setup, logging and failure
cleanup, so they need not equal the sum of the listed phases.

Registration timing covers the request, not a later deferred UI installation;
`CODE MOD MENU` remains the native readiness confirmation. These timings are
elapsed seconds, separate from guest fuel and execution deadlines. Guest calls
remain serialized, and components are compiled afresh each launch without a
compilation cache.

## Calls

- `report(message: string)` requires `diagnostics`.
  The host attaches the manifest ID and replaces non-printable characters before
  passing the message to its trusted diagnostic handler. It grants no game access.
- `add-menu-button(id: string, label: string, message: string)` requires API 0.2.0
  and `main-menu`. It stages an owned native row and a click confirmation.
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

## Native menu buttons

Request `main-menu` in an API 0.2.0 manifest and register during `activate()`:

```python
wit_world.add_menu_button("confirmation", "Mod Launcher", "The sandboxed UI mod is active")
```

The host supplies the calling mod's identity. The item ID follows the same short
identifier rules as mod IDs. Labels contain 1-64 printable characters; messages
contain 1-160. Leading/trailing whitespace and control characters are rejected.
Each mod may own four buttons, with 32 total per game process. Duplicate labels,
duplicate owned IDs and labels already used by the native menu are rejected.
The complete bridge declaration also has a 16 KiB encoded-JSON limit.

After successful guest activation, the launcher publishes the declarations through
the existing bounded Frida channel. The trusted adapter waits for the normally
loaded `ui.screens.hub_screen` module and checks its expected native interface.
It inserts rows before `Exit`, or at the end if no Exit row exists. The game keeps
its renderer, layout, hover, keyboard and mouse handling. Clicking the button
shows its confirmation for 2.5 seconds using the native message display. This
capability does not invoke guest `on_event()` or arbitrary game actions on click.

Declarations are plain values, never Python, target paths, callbacks or commands.
Host work is published outside the guest's fuel/deadline window, using a separate
bounded bridge request. A failed/trapping mod loses its own rows; scope shutdown
removes all rows and restores the owned activation wrapper. An unsupported menu
disables UI mods while diagnostics-only neighbours remain active. Deferred status
records carry a host-owned counter so a stale failure cannot revoke a later
replacement registry. Starting a plain pack adds no example button.

API 0.2.0's WIT uses standard feature annotations to include only the imports
requested by the manifest. Our builder selects these `--features` switches; authors
do not edit WIT or manage the switches themselves. The switches select bindings,
not permissions: runtime admission still checks every import against actual
grants. Unsupported imports are rejected rather than linked to permissive stubs.

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
allow one second of elapsed execution time per exported call; Python component
initialization gets five seconds to accommodate slower hosts. The `wasm` profile
retains a one-second initialization deadline. Both profiles allow 64 host calls
per initialization or exported call. The deadline can interrupt guest code, but
cannot interrupt a blocked host handler; a late return is rejected. See the full
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

The mod author runs this build command and distributes the resulting `mod.wasm`
with the manifest and readable source. The tool is supplied by this repository;
it is not run automatically by the launcher when a player starts the game, and
launcher maintainers do not need to build each author's mod.

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

Startup readiness is loader-owned infrastructure, not an author capability.
The menu keeps New Season, Load Game and Continue Season unavailable until
the selected pack's loading outcomes and native menu registration are settled.
Authors do not supply readiness text, counts or completion messages, and no API
version change is needed for this behaviour.

The launcher discovers and activates sandboxed mods and offers the declarative
native-menu capability. It does not yet deliver general game events or apply
guest event responses to gameplay. Additional
capabilities require reviewed adapters and a versioned contract change.
Compilation and blocked trusted callbacks retain the limits documented in the
sandbox host; there is no claim of OS-level isolation or a bug-free runtime.

References: [WIT](https://component-model.bytecodealliance.org/design/wit.html),
[componentize-py](https://github.com/bytecodealliance/componentize-py),
[SemVer](https://semver.org/).
