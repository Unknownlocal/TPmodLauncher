# SPDX-License-Identifier: MPL-2.0
"""Transfer several checked snapshots into an already-owned runner.

This private adapter neither creates a Store nor binds host providers. Runtime
ownership and owner-attributed host callbacks belong to the caller. It provides
source loading/lifecycle transport before pack loading switches to shared Stores.
"""

from launcher.sandbox import SandboxError
from ._source_policy import (
    MAX_GROUP_SOURCE_BYTES, MAX_GROUP_SOURCE_FILES, MAX_SOURCE_OWNERS,
    MAX_SOURCE_BYTES, MAX_SOURCE_FILES, source_owner, source_path,
)
from .runtime import MAX_TRANSFER_CALLS, SOURCE_FUEL, SOURCE_SECONDS, _chunks
from .source import SourceSnapshot


class SharedSources:
    """Load separate entry objects and address them through original mod IDs."""

    def __init__(self, sandbox, snapshots):
        self.sandbox = sandbox
        try:
            if type(snapshots) is not tuple or not 0 < len(snapshots) <= MAX_SOURCE_OWNERS:
                raise SandboxError("Expected a bounded tuple of source snapshots")
            owners, group_bytes, group_files = set(), 0, 0
            # Check the whole group before the first export. The guest repeats
            # the limits as the actual bytes arrive; cached metadata is not proof.
            for snapshot in snapshots:
                if type(snapshot) is not SourceSnapshot:
                    raise SandboxError("Expected checked source snapshots")
                owner = snapshot.manifest.id
                source_owner(owner)
                if owner in owners:
                    raise SandboxError("Duplicate shared source owner")
                owners.add(owner)
                if type(snapshot.files) is not tuple or len(snapshot.files) > MAX_SOURCE_FILES:
                    raise SandboxError("Source snapshot exceeds its file limit")
                size = 0
                for path, text in snapshot.files:
                    source_path(path)
                    if type(text) is not str:
                        raise SandboxError("Expected UTF-8 source text")
                    size += len(text.encode("utf-8"))
                if size > MAX_SOURCE_BYTES:
                    raise SandboxError("Source snapshot exceeds its byte limit")
                group_bytes += size
                group_files += len(snapshot.files)
            if group_bytes > MAX_GROUP_SOURCE_BYTES or group_files > MAX_GROUP_SOURCE_FILES:
                raise SandboxError("Shared source group exceeds its budget")
            for snapshot in snapshots:
                with sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                    owner = snapshot.manifest.id
                    sandbox.call("declare-source", owner, snapshot.manifest.entry)
                    calls = 0
                    for path, text in snapshot.files:
                        for chunk in _chunks(text.encode("utf-8")):
                            calls += 1
                            if calls > MAX_TRANSFER_CALLS:
                                raise SandboxError("Source transfer exceeds its call limit")
                            sandbox.call("put-owner-source", owner, path, chunk)
            with sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                sandbox.call("seal-sources")
            for snapshot in snapshots:
                with sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                    error = sandbox.call("load-owner-source", snapshot.manifest.id)
                    if error:
                        raise SandboxError((f"Python source load failed for {snapshot.manifest.id}: " + error)[:512])
        except BaseException:
            sandbox.close()
            raise

    def activate(self, owner):
        """Activate the selected loaded entry; the Store remains caller-owned."""
        self.sandbox.call("activate-source", owner)

    def on_event(self, owner, name, payload):
        """Exchange strings with one activated entry, without changing mod IDs."""
        return self.sandbox.call("on-source-event", owner, name, payload)
