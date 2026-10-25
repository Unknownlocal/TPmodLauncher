# SPDX-License-Identifier: MPL-2.0
"""Route the private Wasm exports to source loading and retained regulations."""

import runner_bindings as _bindings

from _regulations.group import RegulationGroup
from _regulations.policy import encode as _encode_regulation
from .source_group import SourceGroup


# Each instantiated runner starts with its own copy of these frozen objects.
# Keeping them at module scope preserves state across separate export calls.
_group = SourceGroup()
_shared_regulations = RegulationGroup(_group)


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

    def declare_source(self, owner, entry):
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

    def begin_regulation_policy(self, epoch, generation, manifest_bytes):
        return _encode_regulation(_shared_call("begin_policy", epoch, generation, manifest_bytes))

    def put_regulation_policy(self, chunk):
        _shared_call("put", "manifest", chunk)

    def seal_regulation_policy_manifest(self):
        return _encode_regulation(_shared_call("seal_manifest"))

    def install_regulation_records(self, page):
        return _encode_regulation(_shared_call("install_records", page))

    def begin_regulation_record(self, slot, bytes):
        _shared_call("begin_record", slot, bytes)

    def put_regulation_record(self, chunk):
        _shared_call("put_record", chunk)

    def seal_regulation_record(self):
        return _encode_regulation(_shared_call("seal_record"))

    def seal_regulation_policy(self):
        return _encode_regulation(_shared_call("seal_policy"))

    def commit_regulation_policy(self, epoch, generation):
        return _encode_regulation(_shared_call("commit_policy", epoch, generation))

    def begin_regulation_action(self, owner, action, epoch, generation, bytes, first):
        return _encode_regulation(_shared_call("begin_action", owner, action, epoch, generation, bytes, first))

    def put_regulation_action(self, chunk):
        _shared_call("put", "action", chunk)

    def seal_regulation_action(self):
        return _encode_regulation(_shared_call("seal_action"))

    def prepare_regulation_slots(self, action):
        return _encode_regulation(_shared_call("prepare", action))

    def validate_resident_regulation(self, action, slot):
        return _encode_regulation(_shared_call("validate", action, slot))

    def bind_source_regulation(self, index, declaration):
        try:
            if not _group.owners:
                raise RuntimeError("Expected a shared source runner")
            _shared_regulations.bind(index, declaration)
        except BaseException as error:
            _shared_regulations.fail()
            # Return a short load error without formatting a traceback through
            # unsupported WASI file calls. The host closes the failed runner.
            return (type(error).__name__ + ": " + str(error))[:512]
        return ""

    def begin_regulation_contexts(self, action, bytes, first):
        return _encode_regulation(_shared_call("begin_contexts", action, bytes, first))

    def put_regulation_contexts(self, chunk):
        _shared_call("put_contexts", chunk)

    def seal_regulation_contexts(self):
        return _encode_regulation(_shared_call("seal_contexts"))

    def finish_regulation_action(self, action):
        _shared_call("finish_action", action)

    def reset_regulation_group(self, epoch):
        return _encode_regulation(_shared_call("reset", epoch))
