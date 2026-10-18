# SPDX-License-Identifier: MPL-2.0
"""Match loaded Python functions to trusted artifact code before race admission.

The host supplies fresh Catalogue.resolve data and the execution runtime's loader
supplies the verified module code root. No author-provided bytecode is read or
executed here. A separately decoded archive root can differ in object identity
from already loaded functions, so matching uses complete code fingerprints.
Only readiness records leave this runtime; live functions remain in Bindings.
"""

import hashlib
import marshal
from types import ModuleType

from .manager import data_copy
from .profiling_discovery import (
    code_inventory, compiled_function_id, compiled_source_identity, namespace_functions, DiscoveryError,
)
from .profiling_plan import Bindings, PlanError, _function_id


class Provider:
    """Refresh at trusted load/rebind/admission events, never on every lap/call.

    Each refresh covers exactly the supplied target records, not all functions
    in a module. Unmentioned bindings stay intact. Explicit invalidate handles
    unload/owner replacement before its old catalogue records disappear.
    """

    def __init__(self, bindings, *, code_bytes=4*1024*1024):
        if type(bindings) is not Bindings or type(code_bytes) is not int or not 1 <= code_bytes <= 16*1024*1024:
            raise ValueError("Expected runtime Bindings and a bounded code fingerprint budget")
        self.bindings, self.code_bytes = bindings, code_bytes

    def _records(self, selection):
        """Validate all target identities before any runtime registration changes."""
        self.bindings._thread()
        if (type(selection) is not dict or set(selection) != {"process", "functions", "unmatched"}
                or selection.get("process") != self.bindings.process
                or type(selection.get("functions")) is not list or not 1 <= len(selection["functions"]) <= 256
                or selection.get("unmatched") != []):
            raise PlanError("stale_catalogue", "Expected resolved targets for this process")
        records, seen = [], set()
        for value in selection["functions"]:
            row = data_copy(value)
            key = _function_id(row["function_id"])
            if key in seen:
                raise ValueError("Duplicate provider target")
            seen.add(key)
            # These fields are trusted catalogue identity, not inferred from a
            # function's spoofable __module__/__qualname__ or current attribute.
            for field in ("artifact_checksum", "binding_generation", "module", "source", "discriminator"):
                if type(row.get(field)) is not str or not row[field]:
                    raise ValueError("Missing provider target identity")
            _function_id(row["artifact_checksum"])
            generation = row["binding_generation"]
            if (not generation.isascii() or not generation.isdecimal() or str(int(generation)) != generation
                    or not 0 <= int(generation) < 2**64):
                raise ValueError("Expected canonical provider generation")
            records.append(row)
        return records

    def _report(self, records, candidates):
        """Apply local changes first, so a lost binding stops its capture promptly."""
        # Preflight the final registry as a whole. Quota/identity rejection must
        # not leave a silently admitted prefix when no host report is returned.
        desired = {key: entry.code for key, entry in self.bindings._entries.items()}
        for row, (code, functions, reason) in zip(records, candidates):
            if reason is None:
                desired[row["function_id"]] = code
            else:
                desired.pop(row["function_id"], None)
        if len(desired) > self.bindings.maximum:
            raise PlanError("resource_limit", "Runtime binding limit")
        if len({id(code) for code in desired.values()}) != len(desired):
            raise PlanError("ambiguous_binding", "One code identity cannot have multiple attributed functions")
        # Withdrawals precede admissions so they free their quota/identity slots.
        # Cleanup failures can still interrupt this transition; they propagate
        # with the capture reservation retained, never as successful readiness.
        for row, (_, _, reason) in zip(records, candidates):
            if reason is not None:
                self.bindings.remove(row["function_id"])
        updates = []
        for row, (code, functions, reason) in zip(records, candidates):
            key = row["function_id"]
            if reason is None:
                token = self.bindings.admit(key, code, functions)
                availability, metrics = "ready", ["calls", "elapsed"]
            else:
                # Removing the runtime entry invalidates an active frozen plan
                # even if the host readiness report is delayed or rejected.
                token, metrics = None, []
                availability = "not_loaded" if reason in ("no_live_binding", "binding_removed") else "unsupported"
            updates.append(dict(
                function_id=key, artifact_checksum=row["artifact_checksum"],
                expected_generation=row["binding_generation"], binding_token=token,
                runtime_kind=self.bindings.runtime_kind, metrics=metrics,
                availability=availability, reason=reason,
            ))
        return dict(process=self.bindings.process, runtime=self.bindings.runtime, bindings=updates)

    def refresh(self, selection, root, module):
        """Verify a selected module against its trusted root, then report readiness.

        All scanning/fingerprinting finishes before changing bindings. Limits or
        malformed inventories raise instead of promoting an arbitrary subset.
        Wrappers are not followed; ambiguous equal code is explicitly unsupported.
        """
        records = self._records(selection)
        try:
            return self._report(records, self._inspect(records, root, module))
        except Exception:
            # Once a trusted target set is accepted, failed verification must not
            # leave old ready bindings/captures claiming uninterrupted coverage.
            # Withdraw them locally; the error still reaches the host, which
            # must not publish readiness from this failed refresh.
            for row in records:
                self.bindings.remove(row["function_id"])
            raise

    def _inspect(self, records, root, module):
        """Finish inventory/code checks before returning any candidate admissions."""
        if len({(row["module"], row["source"], tuple(sorted(row["owner"].items())), row["artifact_checksum"])
                for row in records}) != 1:
            raise ValueError("Refresh one admitted module/owner/artifact at a time")
        rows, codes = code_inventory((root,))
        content, source_hash = compiled_source_identity(root, rows)
        # Descriptor names/lines alone cannot prove this is the artifact indexed
        # by the host. Recompute each ID with its complete root fingerprint;
        # a different body with identical names must not keep the old source ID.
        for row in records:
            owner = (row["owner"]["kind"], row["owner"].get("id"))
            expected_id = compiled_function_id(owner, row["artifact_checksum"], row["module"],
                                               row["source"], source_hash, row["discriminator"])
            if expected_id != row["function_id"]:
                raise PlanError("stale_catalogue", "Trusted root does not match admitted compiled source")
        descriptors = {row[2]: row for row in rows}
        remaining = self.code_bytes - len(content)
        if remaining < 0:
            raise DiscoveryError("Code fingerprint byte limit")
        fingerprints = {}

        def fingerprint(code):
            nonlocal remaining
            if id(code) not in fingerprints:
                # Version 2 excludes reference-sharing flags. Hashes compare code
                # content, including constants/locations, not names alone. There
                # is no marshal.loads or author execution on this path. The byte
                # budget counts serialized output, not peak allocation: marshal
                # produces each trusted code object's bytes before this check.
                content = marshal.dumps(code, 2)
                remaining -= len(content)
                if remaining < 0:
                    raise DiscoveryError("Code fingerprint byte limit")
                # Retain the object too: if another thread replaces __code__, its
                # old address must not be reused for a different cached digest.
                fingerprints[id(code)] = (code, hashlib.sha256(content).digest())
            return fingerprints[id(code)][1]

        expected = {}
        for discriminator, code in codes.items():
            expected.setdefault(fingerprint(code), []).append(discriminator)
        found = {}
        functions = namespace_functions(module)
        namespace = ModuleType.__getattribute__(module, "__dict__")
        for function in functions:
            # A matching imported alias is not evidence that this artifact loaded
            # it. Anchor functions to the loader-verified module's actual globals,
            # ignoring spoofable module/name attributes and foreign namespaces.
            if function.__globals__ is not namespace:
                continue
            code = function.__code__
            matches = expected.get(fingerprint(code), ())
            # Multiple expected functions with identical complete code cannot be
            # distinguished from a separately loaded copy. Do not guess an owner.
            if len(matches) == 1:
                found.setdefault(matches[0], []).append((code, function))
        # A namespace can change while code is fingerprinted. A second bounded
        # scan catches replacement, including aliases that no longer expose the
        # verified object. Do not promote the earlier snapshot as current state.
        if {id(f) for f in namespace_functions(module)} != {id(f) for f in functions}:
            raise DiscoveryError("Namespace changed during verification")
        candidates = []
        for row in records:
            discriminator = row["discriminator"]
            descriptor = descriptors.get(discriminator)
            if descriptor != [row["qualified_name"], row["line"], discriminator, row["kind"]]:
                raise PlanError("stale_catalogue", "Trusted root and target inventory disagree")
            code = codes[discriminator]
            matches = found.get(discriminator, [])
            functions = [function for _, function in matches]
            if row["kind"] != "function":
                reason = "resumable_unsupported"
            elif len(expected[fingerprint(code)]) != 1:
                reason = "ambiguous_code"
            elif not functions:
                reason = "no_live_binding"
            elif any(function.__code__ is not code for code, function in matches):
                reason = "binding_changed"
            elif len({id(code) for code, _ in matches}) != 1:
                # Equal code in multiple live objects would require multiple
                # native rows for one ID. This provider does not merge them.
                reason = "ambiguous_code"
            elif len(functions) > 64:
                raise PlanError("resource_limit", "Live function reference limit")
            else:
                reason = None
            candidates.append((matches[0][0] if matches else code, functions, reason))
        return candidates

    def invalidate(self, selection):
        """Withdraw explicit old targets on unload, before catalogue replacement."""
        records = self._records(selection)
        return self._report(records, [(None, (), "binding_removed") for row in records])
