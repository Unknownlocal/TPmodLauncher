# SPDX-License-Identifier: MPL-2.0
"""Hold distinct source mods together in one guest Python interpreter."""

import importlib
import importlib.util
import sys
from types import ModuleType

from _source_policy import (
    MAX_GROUP_SOURCE_BYTES, MAX_GROUP_SOURCE_FILES, MAX_SOURCE_OWNERS,
    SOURCE_NAMESPACE, module_part, source_owner,
)
from .source_imports import SourceImports
from .source_loader import SourceLoader
from .memory_importer import MemoryImporter


class SourceGroup:
    """Stage all members before imports; route lifecycle calls by manifest ID.

    Names prevent accidental module collisions, not access between mods. Authors
    share an interpreter and may deliberately retain or change each other's data.
    """

    def __init__(self):
        self.owners = {}
        self.entries = {}
        self.bytes = self.files = 0
        self.loaded = 0
        self.sealed = self.failed = False
        self._package(SOURCE_NAMESPACE)
        sys.modules["wit_world"].import_mod = self.import_module

    @staticmethod
    def _package(name):
        package = ModuleType(name)
        package.__path__ = []
        package.__package__ = name
        package.__spec__ = importlib.util.spec_from_loader(name, loader=None, is_package=True)
        sys.modules[name] = package
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(sys.modules[parent], child, package)

    def _owner(self, owner):
        if self.failed or owner not in self.owners:
            raise RuntimeError("Unknown or unavailable source owner")
        return self.owners[owner]

    def declare(self, owner, entry):
        source_owner(owner)
        for part in entry.split("."):
            module_part(part)
        if self.sealed or owner in self.owners or len(self.owners) >= MAX_SOURCE_OWNERS:
            raise ValueError("Invalid or repeated shared source declaration")
        self.owners[owner] = SourceLoader()
        self.entries[owner] = entry

    def put(self, owner, path, chunk):
        loader = self._owner(owner)
        previous_bytes, previous_files = loader.bytes, len(loader.sources)
        loader.put_source(path, chunk)
        self.bytes += loader.bytes - previous_bytes
        self.files += len(loader.sources) - previous_files
        if self.bytes > MAX_GROUP_SOURCE_BYTES or self.files > MAX_GROUP_SOURCE_FILES:
            raise ValueError("Shared source group exceeds its budget")

    def seal(self):
        if self.sealed or not self.owners:
            raise RuntimeError("Invalid or repeated shared source seal")
        self.sealed = True
        # Seal every source before importing any entry. Cross-mod imports then
        # see complete snapshots, independent of the order entries are loaded.
        groups = {}
        for owner, loader in self.owners.items():
            namespace = source_owner(owner)
            self._package(namespace)
            loader.seal(namespace)
            groups[namespace] = (loader.sources, SourceImports(namespace, loader.sources))
        # One finder selects a member by its qualified prefix in a dictionary.
        # Importing a module must not scan every other mod's source finder.
        sys.meta_path.insert(0, MemoryImporter({}, groups))

    def load(self, owner):
        loader = self._owner(owner)
        error = loader.load_entry(self.entries[owner])
        if error:
            self.failed = True
        else:
            self.loaded += 1
        return error

    def import_module(self, owner, name):
        """Explicit cross-mod or dynamic import, using the peer's manifest ID."""
        loader = self._owner(owner)
        if not self.sealed:
            raise RuntimeError("Seal all source snapshots before importing")
        for part in name.split("."):
            module_part(part)
        stem = name.replace(".", "/")
        if stem + ".py" not in loader.sources and stem + "/__init__.py" not in loader.sources:
            raise ModuleNotFoundError("Module is absent from the requested mod")
        return importlib.import_module(loader.namespace + "." + name)

    def activate(self, owner):
        loader = self._owner(owner)
        if self.loaded != len(self.owners):
            raise RuntimeError("Load all source entries before activation")
        loader.activate()

    def on_event(self, owner, name, payload):
        return self._owner(owner).on_event(name, payload)
