# SPDX-License-Identifier: MPL-2.0
"""Trusted runner; author source is supplied only after instantiation.

This module is frozen once at release-build time. The custom importer serves
Python source from guest memory, never from host directories or installed packages.
Each Wasmtime Store gets its own importer, source dictionary and mod instance.
"""

import importlib
import importlib.abc
import importlib.util
# Freeze this initial standard-library profile for later guest imports.
import json
import math
import os
import sys
from types import ModuleType
import runner_bindings as _bindings
from _source_policy import MAX_SOURCE_BYTES, MAX_SOURCE_FILES, source_path
from _regulations.handlers import RegulationHandlers
from _regulations.resident import RegulationResident
from _regulations.policy import decode as _decode_regulation, encode as _encode_regulation


class AuthorWorld:
    """Author facade deliberately excludes the runner's private loading exports."""

    def activate(self):
        raise NotImplementedError

    def on_event(self, name, payload):
        raise NotImplementedError


_author_api = ModuleType("wit_world")
_author_api.WitWorld = AuthorWorld
for _python_name in ("report", "add_menu_button", "register_settings", "save_settings", "register_theme"):
    setattr(_author_api, _python_name, getattr(_bindings, _python_name))
sys.modules["wit_world"] = _author_api

# Capture trusted runtime roots before any author source is accepted or loaded.
_reserved = frozenset(
    name.split(".", 1)[0].casefold()
    for name in (*sys.stdlib_module_names, *sys.modules)
)
_sources = {}
_regulations = RegulationHandlers(_sources)
_resident = RegulationResident(_regulations)
_bytes = 0
_sealed = False
_instance = None
_activated = False


def _resident_call(method, *arguments):
    # Literal export methods below are the sole dispatcher; author facade and
    # event handling never receive these operations. Error strings poison state
    # even before the controller observes them and closes the failed owner.
    try:
        if not _activated:
            raise RuntimeError("Activate before resident regulation setup")
        result = method(*arguments)
        return "" if result is None else _encode_regulation(result)
    except BaseException:
        _resident.fail()
        return '{"error":"Resident regulation failed"}'


def _resident_void(method, *arguments):
    if _resident_call(method, *arguments):
        # Void exports cannot carry an error reply. Trap rather than let an
        # ignored transfer error leave apparently usable policy/action state.
        raise RuntimeError("Resident regulation transfer failed")


def _in_memory_source(event, arguments):
    # CPython fetches source text through fopen when constructing SyntaxError.
    # Decline our virtual filenames before that reaches an unsupported WASI call.
    if event == "open" and isinstance(arguments[0], str) and arguments[0].startswith("<mod>/"):
        raise FileNotFoundError("Mod source is held in memory")


sys.addaudithook(_in_memory_source)


class MemoryImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ordinary modules and explicit packages from the supplied snapshot."""

    def find_spec(self, fullname, path=None, target=None):
        stem = fullname.replace(".", "/")
        package = stem + "/__init__.py"
        module = stem + ".py"
        if package in _sources and module in _sources:
            raise ImportError("Ambiguous module and package")
        if package in _sources:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        if module in _sources:
            return importlib.util.spec_from_loader(fullname, self, is_package=False)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        stem = module.__name__.replace(".", "/")
        path = stem + ("/__init__.py" if module.__spec__.submodule_search_locations is not None else ".py")
        module.__file__ = "<mod>/" + path
        exec(compile(_sources[path], module.__file__, "exec"), module.__dict__)


class RunnerBindings(_bindings.RunnerBindings):
    def put_source(self, path, chunk):
        global _bytes
        if _sealed:
            raise RuntimeError("Source snapshot is sealed")
        source_path(path)
        if path.split("/", 1)[0].removesuffix(".py").casefold() in _reserved:
            raise ValueError("Source conflicts with a trusted or standard-library module")
        size = len(chunk.encode("utf-8"))
        if _bytes + size > MAX_SOURCE_BYTES or (path not in _sources and len(_sources) >= MAX_SOURCE_FILES):
            raise ValueError("Source snapshot exceeds its budget")
        _sources.setdefault(path, []).append(chunk)
        _bytes += size

    def load_source(self, entry):
        global _sealed, _instance
        if _sealed or not entry or any(not part.isidentifier() for part in entry.split(".")):
            raise RuntimeError("Invalid or repeated source load")
        if entry.replace(".", "/") + ".py" not in _sources:
            raise ValueError("Entry module is missing")
        _sealed = True
        for path, chunks in _sources.items():
            _sources[path] = "".join(chunks)
        # All import-time author code starts here, inside the sandbox.
        sys.meta_path.insert(0, MemoryImporter())
        try:
            module = importlib.import_module(entry)
            _instance = module.WitWorld()
        except BaseException as error:
            # Avoid upstream traceback formatting, which otherwise tries to stat
            # source files through denied WASI. The host closes a failed loader.
            return (type(error).__name__ + ": " + str(error))[:1024]
        return ""

    def activate(self):
        global _activated
        if _instance is None or _activated:
            raise RuntimeError("Mod must load once and activate once")
        _instance.activate()
        _activated = True

    def on_event(self, name, payload):
        if not _activated:
            raise RuntimeError("Mod has not activated")
        return _instance.on_event(name, payload)

    def bind_regulation(self, index, declaration):
        if not _activated:
            raise RuntimeError("Activate before binding regulation handlers")
        try:
            _regulations.bind(index, declaration)
        except BaseException as error:
            # As with load_source, avoid upstream traceback formatting through
            # unsupported WASI file/stat calls. The host revokes a failed owner.
            return (type(error).__name__ + ": " + str(error))[:512]
        return ""

    def prepare_regulation(self, index, context, regulation):
        if not _activated:
            raise RuntimeError("Activate before regulation preparation")
        try:
            _regulations.prepare(index, _decode_regulation(context), _decode_regulation(regulation))
        except BaseException as error:
            return (type(error).__name__ + ": " + str(error))[:512]
        return ""

    def prepare_regulations(self, batch):
        if not _activated:
            raise RuntimeError("Activate before regulation preparation")
        try:
            _regulations.prepare_batch(batch)
        except BaseException as error:
            return (type(error).__name__ + ": " + str(error))[:512]
        return ""

    def validate_regulation(self, index):
        if not _activated:
            raise RuntimeError("Activate before regulation validation")
        try:
            return _encode_regulation(_regulations.evaluate(index))
        except BaseException:
            return '{"error":"Regulation handler failed"}'

    def begin_regulation_policy(self, epoch, generation, manifest_bytes):
        return _resident_call(_resident.begin_policy, epoch, generation, manifest_bytes)

    def put_regulation_policy(self, chunk):
        _resident_void(_resident.put, "manifest", chunk)

    def seal_regulation_policy_manifest(self):
        return _resident_call(_resident.seal_manifest)

    def install_regulation_records(self, page):
        return _resident_call(_resident.install_records, page)

    def begin_regulation_record(self, slot, bytes):
        _resident_void(_resident.begin_record, slot, bytes)

    def put_regulation_record(self, chunk):
        _resident_void(_resident.put_record, chunk)

    def seal_regulation_record(self):
        return _resident_call(_resident.seal_record)

    def seal_regulation_policy(self):
        return _resident_call(_resident.seal_policy)

    def commit_regulation_policy(self, epoch, generation):
        return _resident_call(_resident.commit_policy, epoch, generation)

    def reset_regulation_career(self, epoch):
        return _resident_call(_resident.reset, epoch)

    def begin_regulation_action(self, action, epoch, generation, bytes, first):
        return _resident_call(_resident.begin_action, action, epoch, generation, bytes, first)

    def put_regulation_action(self, chunk):
        _resident_void(_resident.put, "action", chunk)

    def seal_regulation_action(self):
        return _resident_call(_resident.seal_action)

    def prepare_regulation_slots(self, action):
        return _resident_call(_resident.prepare, action)

    def validate_resident_regulation(self, action, slot):
        return _resident_call(_resident.validate, action, slot)

    def discard_regulation_action(self, action):
        _resident_void(_resident.discard, action)

