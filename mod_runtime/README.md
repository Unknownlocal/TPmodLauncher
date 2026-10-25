# Mod runtime architecture

`mod_runtime` owns the trusted Python runner and source loading. It runs author
Python inside Wasm in the external launcher. `PackMods` creates one Wasmtime
Store for all source mods in its selected pack, holding their shared Python
interpreter and memory. Each prebuilt Wasm component has a separate Store.
Identical compiled code is shared on the execution thread. Author source is
never imported into the launcher or game interpreter.

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
Shared runners use `launcher/shared_runner_host.py` to select an admitted mod's
table by ID. The runner imports only those five operations, each carrying the
mod ID. The author methods keep their usual arguments; their module supplies
the ID before crossing into the launcher.

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

Each source owner has a `SourceLoader`, holding its files and entry object.
`MemoryImporter` reads that same table.
The `wit_world` API exposes diagnostics,
menu, UI settings and palette operations. Trusted host providers own native
registration and persistence. Source transfer and loading remain private host
operations.

`source_transfer.py` holds the loading limits and splits source into bounded
UTF-8 chunks. Each owner's transfer and entry import have separate allowances;
sealing the complete group has another. Initialisation has its own startup
limits. Activation is explicit, and activation and events use ordinary call
budgets. A failure closes the shared runner and releases its registrations.

`launcher/shared_source_mod.py` applies the launcher's lifecycle and bounded JSON
events to each member of the shared runner. Prepared code is used
only when its portable bytes match a fresh read of the verified runner.

## Shared source loading

`shared_sources.py` supplies a private loading adapter for a runner created by
its caller. It transfers several verified snapshots, then loads a separate entry
object for each original mod ID. Transfer completes for every member before
the first entry is imported. Loading does not activate entries; activation and
events address each entry by its mod ID. A loading failure closes the whole
shared runner. The adapter creates no Store and binds no host providers.

Under `_loading/`, `source_group.py` holds the members and their entry names.
Each member uses its own `SourceLoader` and a module-name prefix derived from
its ID. `source_imports.py` resolves ordinary absolute imports such as
`import rules` within that member's source. Relative imports use Python's real
package names. This avoids collisions between two mods' `rules.py` files without
rewriting source or replacing the global import table between calls.

`wit_world.import_mod(mod_id, module_name)` explicitly imports another member's
module, returning the same Python object that member uses. It also supports
dynamic imports that specify the mod ID. Dynamic relative imports through
`importlib` work with the module's `__package__`; an absolute
`importlib.import_module("rules")`
cannot choose between members and must use the helper instead. Module names
organise loading; they do not prevent mods accessing one another's Python state.

Each mod retains its 1 MiB/256-file source limit. A shared group separately caps
source at 8 MiB, 4096 files and 128 owners. Transfer chunks remain at most 4096
bytes; this is not a file-size limit. The Store's memory and execution limits
still apply after admission.

## Shared runtime ownership and host calls

`shared_runtime.py` owns one `SharedPythonRunner`, including its Store, source
loading and group cleanup. `source_handle.py` supplies a `SourceHandle` for each
member, carrying its manifest and availability. Activation and string events
call the shared runner directly with that member's ID. Closing the group, or a
guest failure, makes every member unavailable and runs group cleanup once.
Creation, calls and closure stay on the Store's creating thread.

Each member imports a `wit_world` module whose host methods are bound to its ID.
`author_world.py` builds those modules and `source_imports.py` returns the right
one for ordinary `import wit_world` and `from wit_world import ...` statements.
A peer's imported function keeps its original module's host identity, including
when another entry calls it later. No mutable global current-mod value is needed.
Fetching the global module directly through `sys.modules` or
`importlib.import_module("wit_world")` bypasses this selection. That common module
contains the base class and import helper; it has no host methods. Source modules
receive their own bound API through the import resolver.

`launcher/shared_runner_host.py` checks that every call names an admitted group
member and selects that member's fixed provider table. Diagnostics, menus,
settings and themes retain their existing author arguments; their private Wasm
imports carry the ID. Ordinary permission checks still apply to that table.
Mods share Python objects and can borrow one another's API modules, so these
tables organise routing rather than isolate mods from their peers. Calls cannot
select providers outside the admitted group or arbitrary launcher functions.

Optional absolute request deadlines bound the existing execution allowance.
Requests already expired before entry leave the group usable. A guest or host
operation that exceeds its allowance closes the group; a late host return cannot
be accepted as success. The Store's existing fuel, memory and host-call limits
remain in force.

`PackMods` owns the shared runner for the game session. Import or activation
failure closes all source peers, including members not yet activated. UI
declarations remain staged until startup completes. Shared regulation storage
uses its own private operations. The runner interface remains private.

## Resident regulation validation

The private 0.4.0 interface binds and evaluates regulation handlers inside Wasm.
Public source admission accepts API 0.2.0/0.3.0. The resident exports provide no
new mod-facing methods or host imports and do not route through lifecycle events.
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
| `group.py` | Share records and action inputs across distinct owner residents |
| `contexts.py` | Check a shared proposal table and resolve its numbered references |
| `policy.py` and `values.py` | Check shared field rules and calculate resource bounds |

Each handler receives its own ordinary dictionaries and lists. Stable regulation
records are not encoded or decoded again for every check. Preparing a group of
up to four invocations has a separate allowance; each handler keeps its own
5 million fuel / 64 host-call allowance. Every phase shares the original 100 ms
action deadline. Framework storage and cleanup count towards their relevant budgets.
Trusted launcher adapters own publication across mods and native readiness.

For a shared runner, `launcher/regulations/shared_handler_runner.py` binds each
owner's admitted functions within that owner's source package. A peer's function
cannot stand in for an owned handler merely because its local filename matches.
Each table keeps its 32-binding limit; there is no 32-binding limit for the whole
shared runner. Source-owner and regulation-session admission limits still apply.

`RegulationGroup` holds those residents and one cache keyed by the existing
record IDs. Owner catalogues borrow common records, while their rules, revisions,
bindings and option data retain their original identities. Installation plans
can receive already installed record IDs, avoiding another transfer for a second
owner. Finishing a publication removes records no active owner needs.

One action transfers its context table once. Owner call packets contain context
indices and their own rule selections; each prepares at most four argument pairs
at a time. Context preparation handles the cached data's first garbage-collector
scan, keeping that work outside later handler allowances.
The shared table holds up to 1024 contexts, with a 4 MiB transfer ceiling
and 4 KiB chunks. The ordinary limit for the combined context and rule still applies.
Finishing the group action checks that no calls remain prepared and every input
context was used. Group reset clears cached records and contexts together while
keeping bindings and the last action ID. Any failed resident operation makes
all residents unavailable. These private operations are separate from lifecycle
events and expose no generic callable API.

The host selects a mod's stored regulation state before calling it. These calls
run one at a time. All owners use the group's record cache and proposal table;
each keeps its own bindings and policy state. Career reset clears the whole
group together. Author host-call identity remains bound to the source module's
mod ID.
`ValidationDispatch` connects shared bindings to publication and execution through
`launcher/regulations/validation/group.py`. It installs each group's common records
once, sends a shared proposal table and checks completion under the original
deadline. Generations and status remain per owner, while failure and reset follow
the shared Store. Pack binding uses one handler for all validator owners and
keeps definition-only owners required without creating empty tables.

## Preparation and session ownership

`preparation.py` reads source and verified runner bytes on a caller-owned executor.
The controller requests ready or pending code through the launcher's compilation
registry, then polls without blocking. Only a missing component starts compilation.
Workers create no Store or game registration. Each preparation owns its source
snapshot and compilation request until the controller takes them once.
Cancellation releases that request; another mod can keep the same compilation.
Abandoned reads or running compilations finish before their results are disposed.

`SourcePreparation` prepares an ordered group against its already checked
manifests. It reads every member without importing Python, checks the
combined source limits, then reads the verified runner once. Any failed member
rejects the group before compilation. `PreparedSources` owns those snapshots and
one compiler request, which a `SharedPythonRunner` can adopt. Closing the result
drops its source copies and releases its request; a running interpreter keeps
its code. A group with one member uses this same preparation and loading path.

`launcher/pack_mods.py` uses grouped preparation for Python source mods and keeps
prebuilt Wasm components independent after checking duplicate IDs. It owns
preparation and executor lifetime with the game session, using the existing
progress, readiness and cleanup rules.
It cancels its own jobs before closing the executor, allowing compilation needed
by another session to finish.
Source loading does not fall back to author-supplied Wasm.
