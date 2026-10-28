# SPDX-License-Identifier: MPL-2.0
"""Hash regulation declarations and source files using shared JSON encodings."""

from collections import Counter
import hashlib
import json


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def finalize(owners, sources, native):
    """Hash [owner, declarations, catalogue, version, files] in a fixed order.

    Cache shared JSON encodings for this pass and feed their bytes directly into
    each owner's hash. Sorted object keys keep the encoding deterministic. The
    hash covers the full data, including source content.
    """
    native_bytes, source_bytes, revisions = {}, {}, {}
    uses = Counter(id(sources[owner].files) for owner in owners)
    for owner, model in owners.items():
        source = sources[owner]
        keys = model.rule_keys() & native.keys()
        digest = hashlib.sha256()
        digest.update(b"[" + _encode(owner) + b"," + _encode(model.declarations()) + b",{")
        for position, key in enumerate(sorted(keys)):
            if key not in native_bytes:
                native_bytes[key] = _encode(key) + b":" + _encode(native[key])
            if position:
                digest.update(b",")
            digest.update(native_bytes[key])
        digest.update(b"}," + _encode(source.manifest.version) + b",")
        identity = id(source.files)
        encoded = source_bytes.get(identity)
        if encoded is None:
            encoded = _encode(source.files)
            # Unique snapshots need no retained buffer; shared immutable tuples
            # are encoded once and released as soon as their last owner is hashed.
            if uses[identity] > 1:
                source_bytes[identity] = encoded
        digest.update(encoded)
        digest.update(b"]")
        revisions[owner] = digest.hexdigest()
        uses[identity] -= 1
        if not uses[identity]:
            source_bytes.pop(identity, None)
    return revisions
