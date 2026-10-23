# SPDX-License-Identifier: MPL-2.0
"""Keep transferred source and its load/activation state together inside Wasm."""

import importlib
import sys

from _source_policy import MAX_SOURCE_BYTES, MAX_SOURCE_FILES, source_path
from .memory_importer import MemoryImporter


def reject_source_file_reads(event, arguments):
    """Stop error formatting from opening our in-memory source as a real file."""
    # CPython tries to fetch source through fopen when constructing SyntaxError.
    # Reject our virtual filenames before that reaches an unsupported WASI call.
    if event == "open" and isinstance(arguments[0], str) and arguments[0].startswith("<mod>/"):
        raise FileNotFoundError("Mod source is held in memory")


class SourceLoader:
    """Transfer, import and activate the current runner's single source mod."""

    def __init__(self):
        # The build entry installs the author API and imports trusted helpers
        # first, so author files cannot replace any of their module roots.
        self.reserved = frozenset(
            name.split(".", 1)[0].casefold()
            for name in (*sys.stdlib_module_names, *sys.modules)
        )
        self.sources = {}
        self.bytes = 0
        self.sealed = False
        self.instance = None
        self.activated = False

    def put_source(self, path, chunk):
        if self.sealed:
            raise RuntimeError("Source snapshot is sealed")
        source_path(path)
        if path.split("/", 1)[0].removesuffix(".py").casefold() in self.reserved:
            raise ValueError("Source conflicts with a trusted or standard-library module")
        size = len(chunk.encode("utf-8"))
        if self.bytes + size > MAX_SOURCE_BYTES or (path not in self.sources and len(self.sources) >= MAX_SOURCE_FILES):
            raise ValueError("Source snapshot exceeds its budget")
        self.sources.setdefault(path, []).append(chunk)
        self.bytes += size

    def load_source(self, entry):
        if self.sealed or not entry or any(not part.isidentifier() for part in entry.split(".")):
            raise RuntimeError("Invalid or repeated source load")
        if entry.replace(".", "/") + ".py" not in self.sources:
            raise ValueError("Entry module is missing")
        self.sealed = True
        for path, chunks in self.sources.items():
            self.sources[path] = "".join(chunks)
        # Source transfer is inert; author code starts only at this import.
        sys.meta_path.insert(0, MemoryImporter(self.sources))
        try:
            module = importlib.import_module(entry)
            self.instance = module.WitWorld()
        except BaseException as error:
            # Avoid traceback formatting, which tries unsupported WASI stat calls.
            # The host closes the runner when it receives this load error.
            return (type(error).__name__ + ": " + str(error))[:1024]
        return ""

    def activate(self):
        if self.instance is None or self.activated:
            raise RuntimeError("Mod must load once and activate once")
        self.instance.activate()
        self.activated = True

    def on_event(self, name, payload):
        if not self.activated:
            raise RuntimeError("Mod has not activated")
        return self.instance.on_event(name, payload)
