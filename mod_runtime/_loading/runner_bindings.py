# SPDX-License-Identifier: MPL-2.0
"""Route the private Wasm exports to source loading and retained regulations."""

import runner_bindings as _bindings

from _regulations.handlers import RegulationHandlers
from _regulations.resident import RegulationResident
from _regulations.group import RegulationGroup
from _regulations.policy import encode as _encode_regulation
from .source_loader import SourceLoader
from .source_group import SourceGroup


# Each instantiated runner starts with its own copy of these frozen objects.
# Keeping them at module scope preserves state across separate export calls.
_loader = SourceLoader()
_regulations = RegulationHandlers(_loader.sources)
_resident = RegulationResident(_regulations)
_group = SourceGroup()
_shared_regulations = RegulationGroup(_group)


def _single_source():
    if _group.owners:
        raise RuntimeError("Cannot mix individual and shared source protocols")


def _resident_call(method, *arguments):
    # Author lifecycle events never dispatch these operations. A bad private
    # operation poisons resident state before the host sees its error reply.
    try:
        if not _group.owners and not _loader.activated:
            raise RuntimeError("Activate before resident regulation setup")
        result = (_shared_regulations.call(method, *arguments) if _group.owners
                  else getattr(_resident, method)(*arguments))
        return "" if result is None else _encode_regulation(result)
    except BaseException:
        (_shared_regulations if _group.owners else _resident).fail()
        return '{"error":"Resident regulation failed"}'


def _resident_void(method, *arguments):
    if _resident_call(method, *arguments):
        # A void export cannot reply with an error; trap so a failed transfer
        # cannot appear successful and leave policy/action state in use.
        raise RuntimeError("Resident regulation transfer failed")


def _shared_call(method, *arguments):
    """A failed shared operation revokes all resident state before trapping."""
    try:
        if not _group.owners:
            raise RuntimeError("Expected a shared source runner")
        return getattr(_shared_regulations, method)(*arguments)
    except BaseException:
        _shared_regulations.fail()
        raise RuntimeError("Shared regulation operation failed") from None


class RunnerBindings(_bindings.RunnerBindings):
    """Keep the fixed private exports separate from their loading/policy state."""

    def put_source(self, path, chunk):
        _single_source()
        _loader.put_source(path, chunk)

    def load_source(self, entry):
        _single_source()
        return _loader.load_source(entry)

    def activate(self):
        _single_source()
        _loader.activate()

    def on_event(self, name, payload):
        _single_source()
        return _loader.on_event(name, payload)

    def declare_source(self, owner, entry):
        if _loader.sources or _loader.sealed:
            raise RuntimeError("Cannot mix individual and shared source protocols")
        _group.declare(owner, entry)

    def put_owner_source(self, owner, path, chunk):
        _group.put(owner, path, chunk)

    def seal_sources(self):
        _group.seal()

    def load_owner_source(self, owner):
        return _group.load(owner)

    def activate_source(self, owner):
        _group.activate(owner)

    def on_source_event(self, owner, name, payload):
        return _group.on_event(owner, name, payload)

    def bind_regulation(self, index, declaration):
        if not _loader.activated:
            raise RuntimeError("Activate before binding regulation handlers")
        try:
            _regulations.bind(index, declaration)
        except BaseException as error:
            # As with source loading, return a short error without formatting
            # a traceback through unsupported WASI file/stat calls.
            return (type(error).__name__ + ": " + str(error))[:512]
        return ""

    def begin_regulation_policy(self, epoch, generation, manifest_bytes):
        return _resident_call("begin_policy", epoch, generation, manifest_bytes)

    def put_regulation_policy(self, chunk):
        _resident_void("put", "manifest", chunk)

    def seal_regulation_policy_manifest(self):
        return _resident_call("seal_manifest")

    def install_regulation_records(self, page):
        return _resident_call("install_records", page)

    def begin_regulation_record(self, slot, bytes):
        _resident_void("begin_record", slot, bytes)

    def put_regulation_record(self, chunk):
        _resident_void("put_record", chunk)

    def seal_regulation_record(self):
        return _resident_call("seal_record")

    def seal_regulation_policy(self):
        return _resident_call("seal_policy")

    def commit_regulation_policy(self, epoch, generation):
        return _resident_call("commit_policy", epoch, generation)

    def reset_regulation_career(self, epoch):
        return _resident_call("reset", epoch)

    def begin_regulation_action(self, action, epoch, generation, bytes, first):
        return _resident_call("begin_action", action, epoch, generation, bytes, first)

    def put_regulation_action(self, chunk):
        _resident_void("put", "action", chunk)

    def seal_regulation_action(self):
        return _resident_call("seal_action")

    def prepare_regulation_slots(self, action):
        return _resident_call("prepare", action)

    def validate_resident_regulation(self, action, slot):
        return _resident_call("validate", action, slot)

    def discard_regulation_action(self, action):
        _resident_void("discard", action)

    def bind_source_regulation(self, index, declaration):
        try:
            if not _group.owners:
                raise RuntimeError("Expected a shared source runner")
            _shared_regulations.bind(index, declaration)
        except BaseException as error:
            _shared_regulations.fail()
            # Binding failures use the same short reply as individual binding,
            # avoiding traceback formatting through unsupported WASI file calls.
            return (type(error).__name__ + ": " + str(error))[:512]
        return ""

    def select_regulation_owner(self, owner):
        _shared_call("select", owner)

    def begin_regulation_contexts(self, action, bytes, first):
        return _encode_regulation(_shared_call("begin_contexts", action, bytes, first))

    def put_regulation_contexts(self, chunk):
        _shared_call("put_contexts", chunk)

    def seal_regulation_contexts(self):
        return _encode_regulation(_shared_call("seal_contexts"))

    def finish_regulation_action(self, action):
        _shared_call("finish_action", action)

    def finish_regulation_publication(self):
        _shared_call("finish_publication")

    def reset_regulation_group(self, epoch):
        return _encode_regulation(_shared_call("reset", epoch))
