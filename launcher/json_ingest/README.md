# Shared JSON ingestion

This launcher-owned package decodes bounded UTF-8 bytes into indexed `JsonObject`
nodes, arrays and scalars. It performs no file I/O, document-schema conversion,
source admission, native binding or mod-code execution. Document adapters supply
their own byte/BOM and duplicate policies; regulation integration is a separate
changeset.

`decode(content, max_bytes=..., allow_bom=False)` checks the complete byte length
before decoding, including any BOM. UTF-8 BOM support is explicit. Malformed
encoding/syntax and non-JSON constants are rejected with a `ValueError` retaining
the original cause. Objects are indexed once; lookup uses the field index.

Decoding records duplicate names without rejecting the object. Last values and
duplicate markers remain separate so callers can inspect selected subtrees:

- `node.member(name, default=..., duplicates="reject")` selects one field without
  inspecting siblings or validating its descendants. `duplicates="last"` selects
  the final occurrence when that caller permits duplicate names.
- `node.materialize(exclude=(), duplicates="reject")` copies selected fields
  recursively to ordinary dictionaries/lists, applying its duplicate policy.
- `materialize(value, duplicates="reject")` handles decoded array/scalar roots
  and selected subtrees as well as objects.

Raw nodes belong to the caller and should be treated as read-only. Materialized
containers are independent copies. Numeric overflow is rejected when a value is
materialized, allowing excluded subtrees to remain semantically unexamined.
Parsing itself still consumes the bounded document and uses Python's decoder
resource limits. Domain depth/node limits belong to the consuming adapter.

Adapters can select last-value-wins materialization for native data and strict
materialization for enabled extensions, leaving disabled sections unconsumed.
Existing JSON consumers retain their decoders until separately migrated with
compatibility checks. No new guest API or capability is exposed.
