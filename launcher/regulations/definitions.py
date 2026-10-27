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
    Public outward views are independent copies of the retained declarations.
    """

    def __init__(self, owners, revisions, references):
        # Ownership transfers here after conversion has finished. Revalidating or
        # copying in the model would repeat the converter's work; use read() or
        # from_bytes() when starting with untrusted file content.
        self._owners, self._revisions = owners, revisions
        # Keep category references, not the full native document.
        # Trusted catalog preparation borrows them without re-reading or cloning.
        self._references = references

    @classmethod
    def from_bytes(cls, content, enabled_sources, *, scopes, native_categories=()):
        """Convert bounded bytes using trusted admission/mapping inputs."""
        owners, revisions, references = convert(content, enabled_sources, scopes=scopes,
                                               native_categories=native_categories)
        # Do not construct a candidate with only the owners that happened to pass.
        return cls(owners, revisions, references)

    def _catalog_inputs(self):
        """Borrow retained inputs for trusted read-only catalog preparation only."""
        return self._references, self._owners, self._revisions

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
    def native_rules(self):
        """Copy the native check switches for each admitted owner."""
        return {owner: deepcopy(data.native_rules) for owner, data in self._owners.items() if data.native_rules}

    @property
    def revisions(self):
        # Digests are immutable strings, so only the owner-to-digest map is copied.
        return dict(self._revisions)

    @property
    def custom_categories(self):
        """Copy enabled owners' categories for the fixed native loading adapter."""
        return deepcopy([category for owner in self._owners.values() for category in owner.categories])

    @property
    def custom_scripts(self):
        """Copy only enabled owners' scripts; native policy remains game-owned."""
        return deepcopy([row for owner in self._owners.values() for row in owner.scripted_changes])
