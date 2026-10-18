# Regulation definition admission

This private package implements the definition-loading part of the planned
regulation adapter. It does not publish rules, bind handlers, install native
validators or enable public API 0.4.0. Existing API versions remain unchanged.

`PackMods.prepare_regulations()` is a trusted, one-shot preparation seam after
pack activation. Enabled owners come from its retained, active Python source
instances requesting `regulations`; closed, failed and prebuilt-component mods
cannot supply owners. Source snapshots are retained after admission rather than
reopening `src/`. Current manifest versions cannot request this capability, so
ordinary sessions neither read the extension file here nor gain new resources.
Handler binding/readiness must be connected before any staged view is published.

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
another owner's metadata fits. Full runtime context/upcoming-policy checks still
belong to dispatch integration.

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
  and execution belong to later sandbox binding, not host imports.
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
