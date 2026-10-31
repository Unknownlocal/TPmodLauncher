# SPDX-License-Identifier: MPL-2.0
"""Optional diagnostic counters; clean timings use an unpatched process."""

from collections import Counter
from contextlib import ExitStack
import json
import socket
import time
from unittest.mock import patch

from memory import process_memory


class Meter:
    def __init__(self, enabled):
        self.enabled, self.phase = enabled, None
        self.counts, self.exports, self.memory, self.errors = {}, {}, {}, {}
        self._patches = ExitStack()

    def count(self, key, value=1):
        if self.phase is not None:
            self.counts.setdefault(self.phase, Counter())[key] += value

    def __enter__(self):
        if not self.enabled:
            return self
        dumps, loads, send, recv = json.dumps, json.loads, socket.socket.send, socket.socket.recv

        def encode(value, *args, **kwargs):
            result = dumps(value, *args, **kwargs)
            self.count("json_dumps")
            self.count("stable_dumps", int('"stable_token"' in result))
            self.count("context_dumps", int('"context_token"' in result))
            return result

        def decode(value, *args, **kwargs):
            self.count("json_loads")
            marker = b'"stable_token"' if type(value) in (bytes, bytearray) else '"stable_token"'
            self.count("stable_loads", int(marker in value))
            return loads(value, *args, **kwargs)

        def write(connection, data, *args, **kwargs):
            result = send(connection, data, *args, **kwargs)
            self.count("socket_sent_bytes", result)
            return result

        def read(connection, count, *args, **kwargs):
            result = recv(connection, count, *args, **kwargs)
            self.count("socket_received_bytes", len(result))
            return result

        for owner, name, function in ((json, "dumps", encode), (json, "loads", decode),
                                       (socket.socket, "send", write), (socket.socket, "recv", read)):
            self._patches.enter_context(patch.object(owner, name, function))
        return self

    def catalog(self, module):
        if not self.enabled:
            return
        original = module.encode

        def encode(value):
            self.count("catalog_payload_encodes")
            self.count("catalog_base_encodes", int("stable_token" in value))
            return original(value)

        self._patches.enter_context(patch.object(module, "encode", encode))

    def bind(self, sandbox):
        if not self.enabled:
            return
        original = sandbox.call

        def call(name, *args, **kwargs):
            phase = self.phase
            budget = kwargs.get("fuel", sandbox._store.get_fuel())
            start = time.perf_counter_ns()
            try:
                result = original(name, *args, **kwargs)
                fuel = budget - sandbox._store.get_fuel()
            except Exception as error:
                self.count("guest_failed_calls")
                if phase is not None:
                    self.errors.setdefault(phase, []).append({"export": name, "error": str(error)})
                raise
            finally:
                if phase is not None:
                    memory = process_memory()
                    self.memory[phase] = {key: max(memory[key], self.memory.get(phase, {}).get(key, 0)) for key in memory}
            if phase is not None:
                totals = self.exports.setdefault(phase, {}).setdefault(name, Counter())
                totals.update(calls=1, fuel=fuel, elapsed_ns=time.perf_counter_ns() - start)
                self.count("guest_string_bytes", sum(len(value.encode("utf-8")) for value in args if type(value) is str))
                self.count("stable_guest_calls", int(any('"stable_token"' in value for value in args if type(value) is str)))
            return result

        self._patches.enter_context(patch.object(sandbox, "call", call))

    def snapshot(self):
        return {"counts": self.counts, "exports": self.exports, "memory": self.memory, "errors": self.errors}

    def __exit__(self, *error):
        self._patches.close()
