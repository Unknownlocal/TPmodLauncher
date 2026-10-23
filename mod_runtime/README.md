# Mod runtime architecture

`mod_runtime` owns the trusted Python runner and source loading. It runs author
Python inside Wasm in the external launcher. Each mod has its own Wasmtime Store,
which holds its Python interpreter, memory and execution state. Mods have separate
permissions, while identical compiled runner code is shared on the execution
thread. Author source is never imported into the launcher or game interpreter.

## Runner files and verification

`bundle.py` defines the runner's private interface and checks its Wasm, trusted
source and upstream notices against the file list, sizes and hashes in
`bundle.json`. Installed bundles live under `libs/mod-runners`. Every component
read checks its bytes again. Release copying writes `bundle.json` last into a
new directory, so incomplete copies cannot pass verification. The launcher
distribution supplies this file and the contents it describes.

`build.py` builds the fixed `guest.py` and source/regulation helpers against
`0.4.0/runner.wit` using the pinned developer component builder. It checks the
interface and publishes a fresh bundle. Player startup uses the installed bundle;
mod authors supply their Python source and manifest.

`contract.py` records host operations, their parameter/result types and required
permissions. `launcher/runner_host.py` supplies a separate operation table for
each mod. Ungranted operations reject calls before a provider runs. The sandbox
checks signatures and enforces memory, execution and host-call limits.

## Source admission

`source.py` reads API 0.2.0/0.3.0 Python source mod manifests and takes an immutable
copy of UTF-8 files under `src/`. Limits bound file count, size, path depth and
entry names. `_source_files.py` checks ordinary directories and rejects links,
reparse points, hard links and special files. Reads keep checked directories
open to protect against replacement.

`_source_policy.py` supplies the same portable naming and size rules to the host
and guest. The host checks layout without importing source. The guest reserves
its existing module names before loading author files.

## Guest loading and execution

`guest.py` is the fixed build entry. It makes `json`, `math` and `os` available
to author imports, installs the `wit_world` API and exposes the private runner
exports. Modules under `_loading/` handle source and lifecycle state inside Wasm:

| Module under `_loading/` | Responsibility |
| --- | --- |
| `author_world.py` | Supply the author base class and `wit_world` host operations |
| `memory_importer.py` | Import modules and packages from transferred source |
| `source_loader.py` | Limit source transfer, load the entry object and track activation |
| `runner_bindings.py` | Route private exports to the loader and retained regulations |

Each Store retains one `SourceLoader` with its source table and entry object.
`MemoryImporter` reads that same table. The `wit_world` API exposes diagnostics,
menu, UI settings and palette operations. Trusted host providers own native
registration and persistence. Source transfer and loading remain private host
operations.

`runtime.py` transfers one checked source snapshot in bounded UTF-8 chunks,
then imports it inside Wasm. Transfer and import share an aggregate fuel,
elapsed-time and host-call allowance. Initialisation has its own startup allowance;
activation and later events retain ordinary call budgets. Activation is explicit.
A failure closes the instance and releases its owned registrations.

`launcher/source_mod.py` applies the launcher's lifecycle, bounded JSON events
and host-owned diagnostic/UI identity to the runner. Prepared code is used
only when its portable bytes match a fresh read of the verified runner.

## Resident regulation validation

The private 0.4.0 interface binds and evaluates regulation handlers inside Wasm.
Public source admission accepts API 0.2.0/0.3.0. The resident exports provide no
new author facade methods or host imports and do not route through lifecycle events.
A matching built bundle is required whenever its trusted guest sources change.

Handler bindings become fixed when resident setup begins. Each owner keeps checked
regulation data in memory across actions. Updates are staged and checked before
commit makes them active. Generations and action IDs reject stale requests.
Reset drops career data and actions while retaining the owner's binding identity.
Invalid operations disable the affected resident state.

| Module under `_regulations/` | Responsibility |
| --- | --- |
| `handlers.py` | Resolve functions belonging to the admitted source owner |
| `catalog.py` | Store one owner's rules and reuse unchanged records |
| `frozen.py` | Store checked JSON values and reconstruct fresh handler inputs |
| `action.py` | Resolve a proposal's rules and prepare bounded groups of calls |
| `transfer.py` | Check complete UTF-8 transfers before decoding JSON |
| `resident.py` | Control setup, staged publication, action lifetime and failure |
| `policy.py` and `values.py` | Check shared field rules and calculate resource bounds |

Each handler receives its own ordinary dictionaries and lists. Stable regulation
records are not encoded or decoded again for every check. Preparing a group of
up to four invocations has a separate allowance; each handler keeps its own
5 million fuel / 64 host-call allowance. Every phase shares the original 100 ms
action deadline. Framework storage and cleanup count towards their relevant budgets.
Trusted launcher adapters own publication across mods and native readiness.

## Preparation and session ownership

`preparation.py` reads source and verified runner bytes on a caller-owned executor.
The controller requests ready or pending code through the launcher's compilation
registry, then polls without blocking. Only a missing component starts compilation.
Workers create no Store or game registration. Each preparation owns its source
snapshot and compilation request until the controller takes them once.
Cancellation releases that request; another mod can keep the same compilation.
Abandoned reads or running compilations finish before their results are disposed.

`launcher/pack_mods.py` dispatches Python source mods and mods with prebuilt Wasm
components after checking duplicate IDs. It owns preparation and executor lifetime
with the game session, using the existing progress, readiness and cleanup rules.
It cancels its own jobs before closing the executor, allowing compilation needed
by another session to finish.
Source loading does not fall back to author-supplied Wasm.
