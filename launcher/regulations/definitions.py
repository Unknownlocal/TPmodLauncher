# SPDX-License-Identifier: MPL-2.0
"""Prepared regulation state; conversion and admission live outside the model."""

from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path

from mod_runtime import _source_files as disk
from . import schema
from .converter import admitted_sources, convert


class RegulationDefinitions:
    """Own an atomic candidate view for binding, never native publication.

    The constructor takes ownership of converter-prepared state. Byte ingestion
    goes through from_bytes(); no JSON can select this model or a host class.
    All outward views are independent copies of the retained declarations.
    """

    def __init__(self, native, owners, revisions):
        # Ownership transfers here after conversion has finished. Revalidating or
        # copying in the model would repeat the converter's work; use read() or
        # from_bytes() when starting with untrusted file content.
        self._native, self._owners, self._revisions = native, owners, revisions

    @classmethod
    def from_bytes(cls, content, enabled_sources, *, scopes, native_categories=()):
        """Convert bounded bytes using trusted admission/mapping inputs."""
        native, owners, revisions = convert(content, enabled_sources, scopes=scopes,
                                            native_categories=native_categories)
        # Do not construct a candidate with only the owners that happened to pass.
        return cls(native, owners, revisions)

    @classmethod
    def read(cls, folder, enabled_sources, *, scopes, native_categories=()):
        """Read one bounded pinned file; with no enabled owners, perform no I/O."""
        sources = admitted_sources(enabled_sources)
        if not sources:
            # An ordinary pack need not contain a regulations file for this seam.
            return None
        with ExitStack() as stack:
            root, parent = disk.root_directory(stack, Path(folder))
            content = disk.read_file(root / "regulations.json", parent, schema.MAX_FILE_BYTES)
        return cls.from_bytes(content, sources, scopes=scopes, native_categories=native_categories)

    @property
    def owner_ids(self):
        """List admitted declarations without materializing their opaque data."""
        return tuple(self._owners)

    @property
    def validators(self):
        """Copy only handler declarations for binding, not full option catalogues."""
        return {owner: deepcopy(data.validators) for owner, data in self._owners.items()}

    @property
    def revisions(self):
        # Digests are immutable strings, so only the owner-to-digest map is copied.
        return dict(self._revisions)

    @property
    def owners(self):
        """Return isolated declarations for binding; this is not readiness."""
        # OwnerDefinition.declarations() borrows internal lists/dicts. Copy at
        # this boundary so consumers cannot mutate retained models or revisions.
        return deepcopy({owner: data.declarations() for owner, data in self._owners.items()})

    @property
    def native_document(self):
        return deepcopy(self._native)

    @property
    def document(self):
        """Return a candidate native view; callers must bind before publishing it."""
        result = self.native_document
        # Extend only the copied native view. Owner-only metadata and handler
        # references stay in owners; they are not native game declaration fields.
        for data in self._owners.values():
            if data.categories:
                result.setdefault("categories", []).extend(deepcopy(data.categories))
            if data.scripted_changes:
                result.setdefault("change_control", {}).setdefault("scripted_changes", []).extend(deepcopy(data.scripted_changes))
        return result
