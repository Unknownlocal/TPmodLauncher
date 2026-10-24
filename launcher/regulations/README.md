# Regulation admission and handlers

This private package reads enabled mods' regulation definitions and binds their
Python handlers inside Wasm. The launcher owns pack lifetime, data publication
and execution. Trusted game adapters supply the validation points. Mod code
cannot select a native function or call the private callable engine.
Public source admission accepts API 0.2.0/0.3.0; regulation permission remains
private to the 0.4.0 interface.

## Definition preparation

`PackMods.prepare_regulations()` runs once after activation. Its owners are live,
accepted source mods with the `regulations` permission. Prebuilt components and
closed or failed source mods cannot add owners. Each eligible mod retains its
checked source copy so preparation never reopens a changed `src/` tree.
Without eligible owners, this path performs no file I/O.

`RegulationDefinitions.read()` pins the selected folder and reads its fixed
`regulations.json`, up to 1 MiB. A missing or unreadable file rejects preparation.
`from_bytes()` accepts bounded bytes and checked source copies. Trusted callers
supply mapped context names and any native fallback categories.

[`json_ingest`](../json_ingest/README.md) decodes UTF-8 and selects fields.
`converter.py` selects native data and enabled mod sections; `owner_converter.py`
checks each section and creates an `OwnerDefinition`. `RegulationSession` checks
collisions and combined limits. `RegulationDefinitions` stores the finished view.
Its outward `owners`, `native_document`, `document` and `revisions` properties
return independent copies. `document` includes owned categories and scripts;
`native_document` preserves native fields without the extension envelope.

`PreparationIndex` reuses option lookups, source paths and payload measurements
within one preparation. Owner data refers to native definitions without copying
the whole native catalogue. `payload.py` checks each owner's actual metadata
against its invocation limits; a shared base measurement cannot approve another
owner's extra data. Revision calculation streams checked declarations, exact
source text and referenced native data into SHA-256. Those digests identify rule
revisions; they do not check game-method compatibility. Preparation caches are
discarded on completion or failure.

## Selected JSON structure

The optional root `mod_extensions` object is keyed by manifest ID. An enabled
section requires integer `schema: 1` and permits `categories`, `option_data`,
`validators` and `scripted_changes`. Disabled sections are left unread beyond
JSON syntax and the whole-file size check. Duplicate consumed extension fields
are rejected; native fields use the game's last-value-wins behaviour.

- Categories require `key`, `label`, `default` and `options`, with optional
  `description` and `neighbours`. Keys start with the owner ID and `.` and cannot
  collide with existing categories.
- Options require consecutive positive integer `id`, unique `key` and printable
  `name`. Optional fields are `summary`, `description` and object `parameters`.
  Neighbours name distinct other options in the same category.
- `option_data[category][option]` holds the mod's extra JSON object for a native
  or owned option. It does not overwrite native fields or contain executable
  operators.
- Validators have exactly `id`, `rule`, `scope` and `handler`. Rules are native
  or owner-local; scopes need trusted mappings. Dotted handler modules must
  exist in that owner's source. Function lookup happens inside Wasm.
- Scripts require `key`, `new_regulation` and positive `effective_season`, with
  optional `id`. `new_regulation` contains the option's `key`. Scripts target
  owned categories. IDs are owner-qualified; omitted IDs use declaration order.
  Native scripts are preserved; duplicate IDs or category/season pairs fail.

Limits are 32 owners, 32 categories/validators per owner, 32 options per custom
category, 128 categories/256 validators per session and 128 scripts per owner.
Each validator's complete context and rule together must fit 4,096 UTF-8 bytes,
1,024 nodes and eight container levels. These are invocation limits, separate
from the 1 MiB file limit. Failed preparation returns no partial owner set.

## Binding and retained validation

`PackMods.bind_regulations()` creates a `RegulationRuntime`. It binds handler
tables once per shared source runner through `SharedHandlerRunner`. Each owner
keeps its own IDs, declarations and generations. Owners declaring data without
validators stay required but need no guest handler table. Direct trusted callers
can also supply individual source runners through `HandlerRunner`.

Handlers are synchronous two-argument functions from the accepted owner source.
Owned re-exports are supported; Python is never imported into the launcher.
Binding uses the source-import allowance: 256 million fuel units and five seconds
per owner. Calls return `{"decision":"allow"}` or
`{"decision":"deny","message":"Reason"}`. Refusals need 1-160 trimmed printable
characters. Conditions stay in mod code.

The [validation package](validation/README.md) owns the combined catalogue,
updates and reference-based dispatch. Rule data stays in memory between actions.
Shared owners reuse native records, and each proposal table transfers once per
runner. Every handler still receives fresh ordinary JSON arguments.

Up to four invocations share a preparation allowance of 64 million fuel units.
Each handler has its own five-million fuel and 64 host-call allowance. The whole
action shares its original absolute 100 ms deadline, including transfer and
cleanup. No new phase restarts that deadline. Failure closes the affected Store;
all source peers in it become unavailable. The private exports use no lifecycle
events and expose no generic callable API.

## Transport and readiness

`RegulationServer` owns one authenticated loopback connection and a bounded queue.
Its I/O thread receives requests and wakes the controller; only controller
`poll()` enters Wasm. `RegulationChannel` uses protocol v2, session tokens,
sequential request IDs, career epochs and acknowledged owner generations.
Socket data cannot supply Python code or native function targets.

`publish(native=..., custom=...)` streams changed rules through `policy-begin`,
ordered `policy-page` rows and `policy-seal`. Native publications supply complete
adopted options; custom publications contain admitted option keys. `None` keeps
installed options while advancing their generation. Private overlays stay in the
launcher. One control deadline covers all pages and commits. Readiness stays
blocked until commit acknowledgement. `reset(epoch)` clears policy while keeping
bindings; a new publication is required and a failed Store cannot be revived.

Actions carry shared contexts/selections and owner binding-slot references.
Frames are bounded to 256 KiB. Larger actions use ordered base64 fragments, up to
8,912,896 decoded bytes/46 parts, assembled before parsing. Large refusal results
also use bounded fragments. All fragments share the original action deadline.
An identified group failure can keep the connection open; broken transport makes
every required owner unavailable. There is no reconnection or stale approval reuse.

`RegulationRuntime` retains required owner identities even after pack cleanup
removes their live entries. Failure expands to every required owner sharing the
Store, including definition-only owners. Wire failure replies name only the
execution owners advertised to the native channel. A connection alone is not
readiness: native interception needs trusted acknowledgement, and execution
owners need committed policy.

`PackMods` reserves eligible rule owners before any source import or activation.
Failure does not release those reservations. Desired UI stays staged while rules
are unready; published UI is cleared on failure, and loading cannot report ready.
Cleanup removes all failed peers' declarations before publishing replacements.
During validation it defers native UI updates until the reply has been released,
so UI acknowledgement cannot block that reply. Shutdown stops transport before
closing the pack's runtimes.
