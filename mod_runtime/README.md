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

`build.py` freezes the fixed `guest.py` against `0.2.0/runner.wit` with the pinned
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

`source.py` parses schema 2, API 0.2.0/0.3.0 manifests and snapshots UTF-8 Python files
under `src/` as immutable, ordered text. File, byte, entry, path and depth limits
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
diagnostics, menu and UI settings operations; source-transfer and loading exports remain a
private host protocol.

`runtime.py` owns one verified bundle and source snapshot through transfer, import,
explicit activation, string events and cleanup. UTF-8 chunks are accumulated and
joined before import. Aggregate byte, file and transfer-call checks bound the
input; a sandbox phase shares fuel, elapsed time and host-call count across
transfer and import. Interpreter initialization has its startup allowance, while
activation and events keep the ordinary callback budgets.

Optional compiler results are adopted only when their portable bytes match the
verified runner. Cleanup ownership starts before admission and passes to the
sandbox until closure. `launcher/source_mod.py` adds the shared launcher lifecycle,
bounded JSON validation and host-owned diagnostic/menu identity to this runtime.

## Worker and session ownership

`preparation.py` snapshots source, rechecks admitted metadata, verifies runner
bytes and compiles on a caller-owned executor. Workers create no Store or game
registration. Each job owns its cancellation signal and result until the
controller takes it once. Cancellation prevents queued work or disposes a late
result; active ordinary-file reads and native compilation finish before cleanup.
The controller owns executor lifetime and admission bounds.

`launcher/pack_mods.py` dispatches schema 2 Python source and explicit schema 1
Wasm components after duplicate-ID checks. It owns preparation jobs with the
session's loading scope and uses the existing progress, menu-readiness and
cleanup protocols. Source loading never falls back to author-supplied Wasm.

`launcher/ui_settings.py` owns labelled UI field declarations and per-mod
`settings.json` storage through API 0.3.0's `ui-settings` grant. The host stages
copied row records for the native Options adapter. `_settings_files.py` uses
directory-relative file access and atomic replacement; author paths never enter
the storage API. Saved settings survive instance cleanup, while registrations do
not. Existing API 0.2.0 manifests keep their original capability set.

Normal entry verifies the complete installed runner before creating sessions.
Release preparation verifies and copies that bundle with trusted package sources
and upstream notices. Its exact inventory remains separate from the bundled
interpreter/dependency inventory.
