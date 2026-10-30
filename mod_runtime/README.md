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

`source.py` reads API 0.2.0/0.3.0/0.4.0 Python source mod manifests and takes an immutable
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

`shared_sources.py` supplies `transfer_sources()` for a runner created by its
caller. It transfers several verified snapshots, then loads a separate entry
object for each original mod ID. Transfer completes for every member before
the first entry is imported. Loading does not activate entries; activation and
events address each entry by its mod ID. The caller owns cleanup on failure;
this function creates no Store and binds no host providers.

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
loading and group cleanup. Its read-only `manifests` table holds each admitted
mod's metadata. Activation and string events call the runner with the mod's ID.
The runner's `closed` state records availability for every member. Closing it, or a
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
Activation and event requests already expired before entry leave the group usable.
A guest or host
operation that exceeds its allowance closes the group; a late host return cannot
be accepted as success. The Store's existing fuel, memory and host-call limits
remain in force.

`PackMods` owns the shared runner for the game session. Import or activation
failure closes all source peers, including members not yet activated. UI
declarations remain staged until startup completes. Shared regulation storage
uses its own private operations. The runner interface remains private.

## Resident regulation validation

The private runner interface binds and evaluates regulation handlers inside Wasm.
Source API `0.4.0` admits their `regulations` permission alongside the earlier host
operations. JSON declarations select ordinary Python functions; the resident
exports remain private, add no host calls and bypass lifecycle event dispatch.
A matching built bundle is required whenever its trusted guest sources change.

Handler bindings become fixed when resident setup or the first voting batch begins.
Each owner keeps checked
regulation data in memory across actions. Each career's complete install is staged
and checked before commit makes it active. Generations and action IDs reject
stale requests.
Reset drops career data and actions while retaining the owner's binding identity.
Invalid operations disable the affected resident state.

| Module under `_regulations/` | Responsibility |
| --- | --- |
| `handlers.py` | Resolve functions belonging to the admitted source owner |
| `catalog.py` | Check and store the shared physical record table |
| `frozen.py` | Store checked JSON values and reconstruct fresh handler inputs |
| `action.py` | Resolve a proposal's rules and prepare bounded groups of calls |
| `transfer.py` | Check complete UTF-8 transfers before decoding JSON |
| `view.py` | Check one mod's bindings and choices against the shared table |
| `group.py` | Own the complete install, mod calls, shared inputs and career reset |
| `contexts.py` | Check the single shared proposal and its slot-zero reference |
| `policy.py` and `values.py` | Check shared field rules and calculate resource bounds |
| `voting.py` | Check separate voting inputs and choice/reason replies |
| `votes.py` | Retain one voting snapshot and prepare independent team arguments |

Each handler receives its own ordinary dictionaries and lists. Stable regulation
records are not encoded or decoded again for every check. Preparing a group of
up to four invocations has a separate allowance; each handler keeps its own
5 million fuel / 64 host-call allowance. Every validation phase shares the original 100 ms
action deadline. Framework storage and cleanup count towards their relevant budgets.
Trusted launcher adapters own publication across mods and native readiness.

For a shared runner, `launcher/regulations/shared_handler_runner.py` binds each
owner's admitted functions within that owner's source package. A peer's function
cannot stand in for an owned handler merely because its local filename matches.
Each table keeps its 32-binding limit; there is no 32-binding limit for the whole
shared runner. Source-owner and regulation-session admission limits still apply.

`RegulationGroup` owns one staged or active catalogue, containing one physical
record table and a checked `RegulationView` for each mod. Record descriptions
and values cross once. Each view retains its own bindings, rules and choice
references, without another transfer state or owner-selection cursor.
The catalogue becomes active only after every owner and record has been checked
and the complete commit is acknowledged. Reset releases the whole career graph.

An action transfers one complete shared proposal. Its context-plus-rule argument
fits 4 KiB, so the proposal needs no paging. Owner packets reference slot zero and
run each declared validator once, in order. Policy and owner-packet transfers
still use pages where needed. Preparation copies at most four argument pairs
at a time and handles the cached data's first garbage-collector scan outside
handler allowances. Completion checks that no calls remain and the proposal
was used. Reset clears career data while retaining bindings and the last action
ID. Failed operations make the whole group unavailable. These private operations
are separate from lifecycle events and expose no generic callable API.

Voting uses separate private operations and handler tables, including owners
with no validators. A batch transfers one frozen snapshot and its proposed rule,
then prepares fresh arguments for each controlling team. The snapshot is limited
to 64 KiB and 8,192 values; a batch admits up to 64 team calculations within
384 KiB. Signing keeps its existing 4 KiB limit. Each voting handler receives
5 million fuel, one second and 64 host calls. Preparation has a separate allowance;
the whole batch also has a cap of five seconds, shortened by its caller's deadline.
Only `choice` (`current` or `proposed`) and a nonblank reason of at most 2,048
UTF-8 bytes are accepted. A failed handler or unfinished batch closes the shared
runner; the host returns no partial results. The existing regulation connection
dispatches these batches on the runner's owning thread. Voting-only owners join
publication and career retirement without creating an empty signing catalogue.
Native activation requires the calculation and both review callers to be installed.

Each voting snapshot lasts one batch. Its cached container templates hold scalar
values; preparing a team's input copies those containers and rebuilds nested
ones. Every team receives fresh dictionaries and lists. Persistent rules keep
their flat tuple storage to avoid repeated garbage-collection work. Snapshot,
option and context measurements are combined to check the complete packet,
without measuring those values again.

`ValidationDispatch` connects shared bindings to publication and execution through
`launcher/regulations/validation/group.py`. Mod calls run one at a time against
their own views under the original deadline. Pack binding uses one handler table
for all validator owners and keeps definition-only owners required without empty
guest tables.

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
