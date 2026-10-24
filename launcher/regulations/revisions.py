# SPDX-License-Identifier: MPL-2.0
"""Finalize unchanged revision digests while reusing canonical JSON inputs."""

from collections import Counter
import hashlib
import json


def _encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def finalize(owners, sources, native):
    """Hash the existing [owner, declarations, catalogue, version, files] format.

    SHA-256 depends on byte order, so replacing source/catalogue bytes with their
    own sub-digests would change revisions. Cache their canonical encodings and
    stream those same bytes instead. All caches die with this finalization pass.
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
