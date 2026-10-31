# Shared JSON decoding

This package decodes size-limited UTF-8 JSON into `JsonObject` records, lists and
scalar values. It reads no files and runs no mod code. Callers choose how to
handle byte-order marks (BOMs) and duplicate fields, and check the document's rules.

`decode(content, max_bytes=..., allow_bom=False)` checks the complete byte length
before decoding, including any BOM. UTF-8 BOM support is explicit. Malformed
UTF-8, invalid JSON and non-JSON constants raise `ValueError` with the original
error retained. Each object's field index avoids rescanning fields during lookup.

Decoding keeps duplicate-field information without rejecting the object.
Callers can inspect individual fields or sections:

- `node.member(name, default=..., duplicates="reject")` selects one field without
  inspecting other fields or checking nested values. `duplicates="last"` selects
  the final occurrence when that caller permits duplicate names.
- `node.materialize(exclude=(), duplicates="reject")` copies selected fields
  recursively to ordinary dictionaries/lists, applying its duplicate policy.
- `materialize(value, duplicates="reject")` handles decoded array/scalar roots
  and selected sections as well as objects.

Decoded records belong to the caller and should be treated as read-only.
`materialize` returns independent dictionaries and lists. Non-finite numbers are
rejected when copied, so excluded sections remain unchecked. Decoding still reads
the whole size-limited document under Python's decoder limits. The caller checks
its own limits on nesting and the number of values.

Regulations uses `duplicates="last"` for game fields and rejects duplicate fields
in enabled mod sections. Disabled sections are not copied. Other JSON consumers
use their own decoders. This package supplies no mod API or permission.
