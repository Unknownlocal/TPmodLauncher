# SPDX-License-Identifier: MPL-2.0
"""Route the private Wasm exports to source loading and retained regulations."""

import runner_bindings as _bindings

from _regulations.handlers import RegulationHandlers
from _regulations.resident import RegulationResident
from _regulations.policy import encode as _encode_regulation
from .source_loader import SourceLoader


# Each instantiated runner starts with its own copy of these frozen objects.
# Keeping them at module scope preserves state across separate export calls.
_loader = SourceLoader()
_regulations = RegulationHandlers(_loader.sources)
_resident = RegulationResident(_regulations)


def _resident_call(method, *arguments):
    # Author lifecycle events never dispatch these operations. A bad private
    # operation poisons resident state before the host sees its error reply.
    try:
        if not _loader.activated:
            raise RuntimeError("Activate before resident regulation setup")
        result = method(*arguments)
        return "" if result is None else _encode_regulation(result)
    except BaseException:
        _resident.fail()
        return '{"error":"Resident regulation failed"}'


def _resident_void(method, *arguments):
    if _resident_call(method, *arguments):
        # A void export cannot reply with an error; trap so a failed transfer
        # cannot appear successful and leave policy/action state in use.
        raise RuntimeError("Resident regulation transfer failed")


class RunnerBindings(_bindings.RunnerBindings):
    """Keep the fixed private exports separate from their loading/policy state."""

    def put_source(self, path, chunk):
        _loader.put_source(path, chunk)

    def load_source(self, entry):
        return _loader.load_source(entry)

    def activate(self):
        _loader.activate()

    def on_event(self, name, payload):
        return _loader.on_event(name, payload)

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
