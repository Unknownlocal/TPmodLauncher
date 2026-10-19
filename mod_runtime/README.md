# Mod runtime architecture

`mod_runtime` owns the trusted Python-in-Wasm runner and source loading. It is
separate from game-process control and native hooks. One prebuilt artifact serves
individual mod instances, each with its own interpreter state, Store and grants.
The external launcher owns those instances; author source is never imported into
the host interpreter or game.

## Artifact and private contract

`bundle.py` defines the private ABI and verifies the exact bounded inventory of
runner Wasm, trusted source and upstream notices. Installed artifacts live under
`libs/mod-runners`. Every component read rechecks its bytes, and release copying
uses the previously accepted receipt, publishing that receipt last into a fresh
directory. The receipt detects mismatches against its inventory; trust in that
inventory comes from the launcher distribution.

`build.py` freezes the fixed `guest.py` and source/regulation policy helpers
against `0.4.0/runner.wit` with the pinned
developer componentizer. It checks the resulting interface and publishes a fresh
bundle. Artifact and source-reader imports require only the standard library;
execution modules load the sandbox/compiler explicitly. Player startup needs no
componentizer or per-mod component build.

`contract.py` records the scalar signature and required capability of each host
operation. `launcher/runner_host.py` binds a private table for each mod: granted
operations use trusted providers, while ungranted operations reject calls before
a provider runs. The sandbox checks signatures and enforces execution limits,
including operations invoked during initialization or source import.

## Source admission

`source.py` parses API 0.2.0/0.3.0 Python source mod manifests and snapshots
UTF-8 Python files under `src/` as immutable, ordered text. File, byte, entry, path and depth limits
bound the work. Portable names, explicit package initializers and case checks
reject ambiguous layouts. `_source_files.py` pins ordinary ancestor directories
and rejects linked, reparse, hardlinked and special-file inputs. POSIX reads are
directory-relative; Windows handles prevent write/delete replacement during reads.

`_source_policy.py` shares portable path rules and byte/file limits between the
host and guest. The host validates source layout without importing it. The guest
captures its own standard-library and preloaded module roots before author
loading, then rejects reserved-name collisions during transfer.

## Guest loading and execution

`guest.py` serves author modules from guest memory through an in-memory importer.
Its frozen standard-library profile includes `json`, `math`, `os` and their
runtime dependencies. The public `wit_world` facade exposes the author base class,
diagnostics, menu, UI settings and palette operations. Host providers own native
registration and persistence; the guest exchanges bounded strings through the
private contract. Source-transfer and loading exports remain a private host protocol.

The private ABI binds, prepares and evaluates regulation handlers through dedicated
exports. `_regulations/handlers.py` resolves owned source functions
inside Wasm; `_regulations/policy.py` checks common JSON fields and
`_regulations/values.py` composes exact logical bounds on both sides.
Bounded preparation copies up to four invocations
without calling their handlers. Each prepared slot is then consumed once with
its own ordinary 5M fuel/64 host-call allowance; framework preparation has a
separate 64M fuel bound. Every phase shares the original 100 ms action deadline.
These exports add no host imports or author facade methods and do
not use lifecycle events. This unreleased work remains at private ABI 0.4.0;
public source admission still accepts only API 0.2.0/0.3.0. A matching
rebuilt runner bundle is required when these trusted sources change.

### Resident regulation state (private 0.4.0 development)

The resident exports are the sole regulation execution path. The superseded
full-payload preparation/evaluation exports have been removed in the coordinated
v2 transport cutover. Once a Store begins resident setup its binding table
is sealed. No new author facade
methods, host imports, native targets or event routing are exposed. Only owners
with validation bindings need a resident execution view; definitions without
validators remain a launcher/governance concern.

The state is split by responsibility, with one class per module:

| Module | Owns |
| --- | --- |
| `_regulations/catalog.py` | One owner manifest and its distinct canonical records; active-record reuse by immutable identity |
| `_regulations/action.py` | Checked context/choice/call references and at most four fresh handler argument pairs |
| `_regulations/transfer.py` | One exact-length UTF-8 transfer; JSON is parsed only after completion |
| `_regulations/resident.py` | Epoch/generation fences, staged publication, action lifetime and terminal failure |
| `_regulations/values.py` | Exact byte/node/depth/numeric-work metrics, composition and trusted budget arithmetic |

`bind-regulation` declarations include the trusted source owner alongside handler,
scope and rule. The first policy latches the admitted definition revision; all
later policies, including career replacements, must retain that owner/revision.
The launcher must supply the revision from its admitted definitions, not mod claims.
Positive u64 epochs/generations advance monotonically; action IDs remain monotonic
across career resets so stale discards cannot match a new action.

A policy manifest has exactly `owner`, `revision`, `bindings`, `rules`, `choices`,
`records` and `totals`. Rules are distinct bound rule names. Ordered bindings
contain `{rule: rule_slot, scope: string}` and must match the sealed handler table.
A choice is `[rule_slot, option_key, parameters_slot, option_data_slot]`. Multiple
choices may share an option key with different effective payloads; their local
choice slots disambiguate them. All choices and records must be reachable.

Each record descriptor has `slot`, positive u64 `id`, `kind` (`parameters` or
`option_data`), `bytes`, `nodes`, `depth`, `integer_work`. Slots are consecutive.
`totals` contains the sums of descriptor bytes/nodes/integer_work. Each delivered
record is a plain object, independently decoded/measured once and checked against
its descriptor. An unchanged active ID can be reused only with the same kind and
metrics; it cannot be overwritten by another page. Full adopted-future membership
is input-derived, without a new option quota. Resource limits still apply.

Policy begin advances the required generation before any data arrives. Sealing
checks completeness; commit alone makes the staged view active. Reset drops
policy, transfers and actions while retaining the sealed owner/binding identity.
Invalid operations poison resident state; void-export errors trap, and string
exports return a fixed error that trusted callers must treat as owner failure.
An ignored error cannot revive the old generation. Multi-owner publication and
native readiness remain controller responsibilities in the later cutover pass.
For a selection/generation change with unchanged membership/payloads, begin with
zero manifest bytes, then seal/commit the existing view. Initial setup cannot
use this shortcut. No manifest or record decoding occurs on that path.

An action packet contains `owner`, `revision` (the admitted definition revision),
`contexts`, `selections`, `calls`. Each selection has `rule`, `evaluation_season`,
`selected`, `current`, `upcoming`; option references are `[choice_slot,
effective_season]`, with nullable upcoming. Calls are ordered `[binding_slot,
context_slot, selection_slot]` triples. All tables are used and bounded to 32 rows.
The handler's opaque revision also identifies the active epoch/generation. Its
envelope and full 4 KiB/1,024-node/depth-eight limits remain unchanged.

Stable record metrics compose with dynamic context/selection metrics without
encoding the payload again. Copies are prepared before author execution. Canonical
action references retire at their last preparation use, so freeing a large context
table never consumes the last handler's ordinary 5M allowance. The final slot is
removed and the action released before that handler runs; unfinished actions have
an explicit discard. Each handler still receives independent ordinary containers.

Trusted callers use `install_fuel`/`entry_fuel` with independently derived counts,
the existing five-second control/100 ms action deadlines, 64M per later four-slot
preparation and 5M/64 host calls per handler. Control counts must include retired
payload/view nodes on replacement, shrinking publication or reset; a small new
view does not make releasing a large old graph free. Cached record IDs do not
require another payload decode. Budget selection and controller/native transport
integration are intentionally separate from guest storage in this pass.

Development artifacts use matching receipts, source hashes and exact export
inventories despite sharing 0.4.0. Older intermediate 0.4.0 receipts with a different
inventory are not interchangeable. Installed release artifacts are never replaced
by development builds.

`runtime.py` owns one verified bundle and source snapshot through transfer, import,
explicit activation, string events and cleanup. UTF-8 chunks are accumulated and
joined before import. Aggregate byte, file and transfer-call checks bound the
input; a sandbox phase shares fuel, elapsed time and host-call count across
transfer and import. Interpreter initialization has its startup allowance, while
activation and events keep the ordinary callback budgets.

Optional compiler results are adopted only when their portable bytes match the
verified runner. Cleanup ownership starts before admission and passes to the
sandbox until closure. `launcher/source_mod.py` adds the shared launcher lifecycle,
bounded JSON validation and host-owned diagnostic and UI resource identity.

## Worker and session ownership

`preparation.py` snapshots source, rechecks admitted metadata, verifies runner
bytes and compiles on a caller-owned executor. Workers create no Store or game
registration. Each job owns its cancellation signal and result until the
controller takes it once. Cancellation prevents queued work or disposes a late
result; active ordinary-file reads and native compilation finish before cleanup.
The controller owns executor lifetime and admission bounds.

`launcher/pack_mods.py` dispatches Python source mods and mods with prebuilt
Wasm components after duplicate-ID checks. It owns preparation jobs with the
session's loading scope and uses the existing progress, menu-readiness and
cleanup protocols. Source loading never falls back to author-supplied Wasm.

Normal entry verifies the complete installed runner before creating sessions.
Release preparation verifies and copies that bundle with trusted package sources
and upstream notices. Its exact inventory remains separate from the bundled
interpreter/dependency inventory.
