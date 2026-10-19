# Regulation admission and sandbox handlers

This private package admits regulation definitions and binds owned Python
handlers inside Wasm. Pack/session lifetime is launcher-owned; native validators
and rule publication remain separate integration work. Public API 0.4.0 stays unavailable; existing
public API versions remain unchanged.

`PackMods.prepare_regulations()` is a trusted, one-shot preparation seam after
pack activation. Enabled owners come from its retained, active Python source
instances requesting `regulations`; closed, failed and prebuilt-component mods
cannot supply owners. Source snapshots are retained after admission rather than
reopening `src/`. Current manifest versions cannot request this capability, so
ordinary sessions neither read the extension file here nor gain new resources.
`PackMods.bind_regulations()` binds retained owners and starts their private
transport. Trusted native acknowledgement through `confirm_regulations()` is
required before staged UI and completion can publish; actual game mappings
and rule publication remain later integration steps.

`RegulationDefinitions.read()` pins the selected folder and reads at most 1 MiB
from its fixed `regulations.json`. Empty ownership performs no filesystem I/O.
When an eligible owner exists, a missing/unreadable file rejects preparation;
fallback metadata does not supply missing extension declarations.
`RegulationDefinitions.from_bytes()` accepts bounded bytes and admitted source
snapshots for internal conformance checks. Context names and additional native fallback categories are
provided by trusted mappings; the loader does not advertise contexts itself.

Decoding and selected-subtree materialization use the shared
[`json_ingest`](../json_ingest/README.md) primitives. Owner selection and all
regulation-specific declaration checks remain here. `converter.py` selects the
native document and enabled sections; `owner_converter.py` converts each consumed
section into an `OwnerDefinition`. The `RegulationDefinitions` constructor only
stores prepared native/owner/revision state; byte ingestion uses `from_bytes()`.
Each class has its own module. Native and opaque option/parameter data retain
their dictionary shape rather than being normalized into fixed-field models.
The owner model holds categories, option data, validators and scripts; outward
views remain independent dictionaries for later binding/publication.

Conversion is explicit launcher code. JSON cannot choose a model, import a host
class or call the private callable engine.

Preparation has three stages: convert owner declarations, admit them through
`RegulationSession` for shared collision/quota checks, then finalize revisions.
`PreparationIndex` lazily reuses option-key sets, owned source paths and minimum
option-envelope measurements within that attempt. Owner references use a layered
lookup of native and owned categories rather than copying the native catalogue.
Unreferenced native definitions remain outside extension-specific validation.

Minimum envelopes include empty owner metadata. `payload.py` checks each owner's
actual metadata by replacing that placeholder with its measured node count,
container depth and UTF-8 length. A shared native measurement never implies that
another owner's metadata fits. Full invocation bounds and common context fields are checked by dispatch;
scope normalization and adopted upcoming-policy selection remain later work.

Revision finalization streams the existing canonical JSON representation into
SHA-256. It reuses encoded native categories and any shared immutable source-file
tuples, retaining identical digest values. Substituting component digests would
change that representation; this refactor retains the existing format. Unique
source buffers are not cached, and shared buffers are released after their last
owner. All preparation caches are local and discarded on completion or failure.

The optional root `mod_extensions` object is keyed by manifest ID. Each enabled
section requires integer `schema: 1` and permits `categories`, `option_data`,
`validators` and `scripted_changes`. Disabled sections are not materialized or
semantically validated, including duplicate fields inside them. Syntax and the
whole-file byte limit still apply. Duplicate consumed extension fields are
rejected; native fields retain the game's last-value-wins behavior.

- Custom categories require `key`, `label`, `default`, `options`, with optional
  `description` and `neighbours`. Keys start with the owner ID followed by `.`.
  They cannot collide with native, fallback or another owner's definitions.
- Options require consecutive positive integer `id`, unique `key`, and printable `name`;
  optional fields are `summary`, `description` and object `parameters`.
  Neighbours reference distinct other options within the same category.
- `option_data[category][option]` holds an opaque object for a native or owned
  option. It does not overwrite native fields. Neither metadata object has
  operators or executable binding meanings.
- Validators have exactly `id`, `rule`, `scope`, `handler`. Rule references stay
  native or owner-local, scopes must have a trusted mapping, and dotted handler
  modules must exist in that owner's snapshot. Function resolution/signatures
  and execution are resolved inside the sandbox, never through host imports.
- Scripts require `key`, `new_regulation`, positive `effective_season`, and
  optionally a named `id`. `new_regulation` has exactly the option's `key`, using
  the native object format. They target owned custom categories only. IDs are
  owner-qualified; omitted IDs use the declaration index. Existing native scripts
  are preserved; duplicate IDs and category/season targets are rejected.

Limits are 32 owners, 32 categories/validators per owner, 32 options per custom
category, 128 categories/256 validators per session, and 128 scripts per owner.
Known policy data is checked against a minimum invocation envelope for the
4,096-byte/1,024-node/eight-container-level limits. These apply to each validator
invocation payload; the complete `regulations.json` file has the separate 1 MiB
limit described above (tentative).

Preparation is atomic. `owners`, `native_document`, `document` and `revisions`
return independent data. `document` is a candidate merged view for future binding
and publication; `native_document` preserves native fields with the extension
envelope removed. Revisions include the selected declarations, author version
and exact admitted source text, plus referenced native/fallback definitions.
Save-state compatibility, disable/re-enable
transitions, effective dates and native governance remain later work.

## Private sandbox handler dispatch

`HandlerRunner` binds ordinary synchronous two-argument functions from each
owner's admitted source snapshot through dedicated private runner exports. Owned
re-exports are supported; author Python is never imported into the launcher.
Binding shares the source-import allowance (256M fuel/five seconds per owner).

Every invocation receives independent plain JSON copies of context and rule,
limited together to 4,096 UTF-8 bytes, 1,024 nodes and eight container levels.
Common envelope fields are checked on both sides. Handlers return exactly allow
or deny with 1 to 160 trimmed printable message characters. Conditions stay mod code.

Up to four invocations share bounded preparation (64M fuel) for decoding,
checking and copying; this routine invokes no validators. Each prepared slot is
consumed once with its own ordinary 5M fuel and 64 host-call allowance. Preparation
cannot spend a handler's allowance, and siblings cannot spend one another's.
One watchdog covers the original absolute 100 ms action deadline across every
preparation/execution call; selecting a fresh fuel allowance cannot restart it. A full valid
4-KiB invocation uses single-call preparation if batch headers would overflow;
there is no truncation or automatic retry. Failure closes the owned runner.

This requires a matching private ABI 0.4.0 runner bundle. The author facade and
host imports are unchanged; dispatch uses no lifecycle events and exposes no
generic callable or native target access. Native integration remains separate.

## Private persistent transport

`RegulationServer` owns one authenticated loopback connection, a bounded pending
request queue and an I/O thread. The thread only receives/queues/wakes; `poll()`
invokes a trusted evaluator on the creating controller thread. The runtime owns
this server and supplies its endpoint only to trusted native integration.

The native `RegulationChannel` uses session tokens, sequential request IDs and
one absolute action deadline. Frames are length-prefixed and bounded to 256 KiB
for up to 32 expanded invocations; this does not increase per-invocation limits.
An identified owner failure remains unavailable without dropping other owners'
connection. Protocol, disconnect or timeout failure closes the session with no
retry/reconnect or stale approval reuse. Shutdown stops owned I/O.

Fixed client/framing sources are included in the native bridge bootstrap. Its
private wake callback queues controller work without native RPC or guest entry.
Actual game validation mappings remain a separate integration. No author method
or native authority is exposed by this transport. Resident rule-data redesign
follows the staged split.

## Pack lifetime and readiness

`RegulationRuntime` binds required owners once and dispatches queued calls on the
Store's creating controller thread. A connection alone cannot establish readiness:
only trusted acknowledgement of complete native interception permits publication.
Identified handler failure latches its owner unavailable while other owners can
still be checked; transport failure makes all required owners unavailable.
Cleanup does not disable or remove a required declaration.

`PackMods` reserves eligible owners before any activation can publish UI. Failed
activation releases that reservation; successful activation keeps the owner
required until session closure. While unready, desired declarations stay staged
and any published menu/settings/themes are cleared. Loading cannot report complete
until all required owners are ready. Failure cleanup removes local declarations
immediately but defers native UI updates until the validation reply is released,
so UI acknowledgement cannot block that reply. Shutdown stops transport before closing
source Stores in reverse activation order. Public API 0.4.0 remains withheld.
