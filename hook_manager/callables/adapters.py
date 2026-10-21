# SPDX-License-Identifier: MPL-2.0
"""Own and dispatch private guards using the existing hook lifecycle."""

from contextlib import contextmanager
import json
import math
import threading
import time
from weakref import WeakSet

# Normal imports resolve these dependencies; the fixed game bundle supplies them
# earlier in its shared private namespace, where __package__ is None.
if __package__:
    from ..manager import HookError, Manager, data_copy, identifier
    from .binding import CallBinding
    from .result_contract import ResultContract
    from .target import CallableTarget


class CallableAdapters(Manager):
    """Multiplex required owners over one owned wrapper per approved target.

    Reuse Manager's deferred imports, main-thread installation and restoration
    journal. Callback failures latch owners unavailable while retaining guards;
    explicit owner removal is the only path back to unguarded native execution.
    No request/bridge operation can approve targets or register bindings.

    dispatch(owner, calls, deadline) is trusted transport code. It must honor the
    absolute monotonic deadline and return one plain decision per call, without
    nested game queries. Late results are rejected here; trusted Python itself
    is not preempted. Mod handlers will execute in the external sandbox.
    """

    def __init__(self, dispatch=None, *, seconds=.1):
        # Parent installation requires a dispatcher. Our _wrapper bypasses its
        # legacy callback path and uses evaluate, with required-validation failures.
        super().__init__(lambda record: None)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise HookError("Expected a positive finite validation deadline")
        self.evaluate, self.seconds = dispatch, seconds
        # entries maps (owner ID, binding ID) to bindings; inherited groups owns
        # one wrapper per target. Owners become ready only when every group is active.
        self.entries, self.owners = {}, {}
        # Reentrant registry access supports parent poll/status and nested cleanup.
        # Dispatch has its own lock so waiting on a guest never pins the registry.
        self._lock, self._guest = threading.RLock(), threading.Lock()
        self._local, self._threads = threading.local(), WeakSet()
        # Count guarded calls AND whole worker scopes, including nested calls;
        # configuration must remain fixed until both kinds of activity finish.
        self._active = 0

    def _main(self):
        super()._main()
        if self._active:
            raise HookError("Cannot change callable bindings during an operation")

    def approve(self, target):
        """Store a signature-checked catalogue entry without forcing a game import."""
        with self._lock:
            if not isinstance(target, CallableTarget):
                raise HookError("Callable adapters require private callable targets")
            return super().approve(target)

    def enroll(self, thread):
        """Grant a particular native Thread object, before its trusted operation starts."""
        with self._lock:
            self._main()
            if not isinstance(thread, threading.Thread):
                raise HookError("Expected a native operation thread")
            # Object identity avoids granting a later thread a recycled OS ID;
            # weak references do not keep finished Thread objects alive.
            self._threads.add(thread)

    @contextmanager
    def operation(self):
        """Enter a trusted worker operation; thread IDs alone never grant access."""
        current = threading.current_thread()
        with self._lock:
            # Admission and shutdown share the lock: a worker waiting here must
            # recheck enrollment/closure after any intervening configuration.
            if (self.closed or current not in self._threads
                    or getattr(self._local, "operation", False)):
                raise HookError("Native worker operation is not enrolled or is nested")
            self._local.operation = True
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._local.operation = False
                self._active -= 1

    def register(self, owner, bindings):
        """Prepare an owner's complete binding group; no partial owner becomes ready."""
        # Workers use this same lock for admission. None can start between the
        # readiness check, publication, assignments and a possible rollback.
        with self._lock:
            return self._register(owner, bindings)

    def _register(self, owner, bindings):
        """Stage/install under register's lock; roll back immediate install errors."""
        self._main()
        owner = identifier(owner)
        if owner in self.owners or not callable(self.evaluate):
            raise HookError("Owner already registered or no private dispatcher configured")
        if (type(bindings) is not list or not 1 <= len(bindings) <= 32
                or any(not isinstance(item, CallBinding) for item in bindings)
                or len({item.id for item in bindings}) != len(bindings)
                or len(self.entries) + len(bindings) > 256):
            raise HookError("Invalid or oversized owned callable group")
        # Copy integration records. Later mutation of the caller's descriptor does
        # not silently change a published native phase or refusal contract.
        staged = []
        for item in bindings:
            if item.target not in self.targets:
                raise HookError("Target has not been approved by the launcher")
            binding = CallBinding(item.id, item.target, item.project,
                                  ResultContract(item.result.spec), phase=item.phase)
            # A shared wrapper has one native phase/result contract. Owners may
            # supply different projections, but cannot change that call contract.
            for existing in [*self.entries.values(), *staged]:
                if (existing.target == binding.target
                        and (existing.phase != binding.phase
                             or existing.result.spec != binding.result.spec)):
                    raise HookError("Owners disagree on the native call contract")
            staged.append(binding)
        # Pending entries guard loaded targets but cannot advertise complete owner
        # readiness while other targets still await their normal module imports.
        self.owners[owner] = "pending"
        self.entries.update({(owner, item.id): item for item in staged})
        added = []
        try:
            for target in sorted({item.target for item in staged}):
                if target not in self.groups:
                    added.append(target)
                    # This parent record controls wrapper lifetime only; our
                    # wrapper selects guard/query behaviour from CallBinding.
                    super().install(target, [{"target": target, "callback": "internal", "phase": "before"}])
            self.poll()
        except Exception:
            # Remove this attempt's entries and newly created target groups;
            # existing shared groups still belong to their other owners.
            for key in [key for key in self.entries if key[0] == owner]:
                del self.entries[key]
            for target in reversed(added):
                if target in self.groups:
                    super().remove(target)
                    del self.groups[target]
            self.owners.pop(owner)
            raise
        return self.status()

    def _wrapper(self, name, binding, target, original, signature):
        # Base installation owns descriptor preservation and rollback. Always call
        # the outer native function, including all approved decorators, at most once.
        def wrapped(*args, **kwargs):
            return self._invoke(target, original, signature, args, kwargs)
        from functools import update_wrapper
        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped

    def _invoke(self, target, original, signature, args, kwargs):
        with self._lock:
            # Pin configuration for this call and fix owner/binding order, which
            # also determines the first refusal when several validators deny.
            entries = sorted((key, item) for key, item in self.entries.items()
                             if item.target == target.name)
            if entries:
                self._active += 1
        if not entries:
            # Escaped wrappers after explicit removal must not hold the registry
            # lock while native code waits for a worker using another target.
            return original(*args, **kwargs)
        try:
            return self._validate(original, signature, entries, args, kwargs)
        finally:
            with self._lock:
                self._active -= 1

    def _validate(self, original, signature, entries, args, kwargs):
        """Evaluate one call; native exceptions propagate without a fallback retry."""
        # Registration already proved agreement, so one binding supplies the
        # common native codec/phase while each binding retains its own projection.
        codec, phase = entries[0][1].result, entries[0][1].phase
        # Reject invalid arguments before dispatching a malformed call to validators.
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        # Projection, queueing and all owner dispatches share one allowance.
        deadline = time.monotonic() + self.seconds
        result = None
        if phase == "query":
            result = original(*args, **kwargs)  # Native exceptions propagate once.
        affected = {key[0] for key, _ in entries}
        try:
            if phase == "query" and not codec.allowed(result):
                return result  # A mod allowance can never override native refusal.
            if (getattr(self._local, "dispatching", False)
                    or threading.current_thread() is not threading.main_thread()
                    and not getattr(self._local, "operation", False)):
                raise HookError("Unenrolled thread or reentrant validation")
            if any(self.owners[owner] != "ready" for (owner, _), _ in entries):
                # Observing a pending/failed peer blocks this action without
                # treating healthy owners as additional failures.
                return codec.denied("Validation unavailable")
            groups = {}
            for (owner, id), binding in entries:
                affected = {owner}
                # Projection stays native; only its copied result crosses the
                # dispatch boundary, without mutable references into game state.
                snapshot = data_copy(binding.project(dict(bound.arguments), result))
                if len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 4096:
                    raise HookError("Projected invocation exceeds its byte limit")
                groups.setdefault(owner, []).append({"id": id, "snapshot": snapshot})
            # Queue/operation failures affect this action's participants; an
            # identifiable projection or guest failure belongs only to its owner.
            affected = {key[0] for key, _ in entries}
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._guest.acquire(timeout=remaining):
                raise HookError("Validation deadline expired while waiting for dispatch")
            self._local.dispatching = True
            try:
                for owner, calls in groups.items():
                    affected = {owner}
                    if time.monotonic() >= deadline:
                        raise HookError("Validation deadline expired")
                    decisions = data_copy(self.evaluate(owner, calls, deadline))
                    if time.monotonic() >= deadline:
                        raise HookError("Validation deadline expired")
                    if any(self.owners[key[0]] != "ready" for key, _ in entries):
                        # Nested validation can latch failure while an outer
                        # dispatch returns allow; that allowance must not commit.
                        return codec.denied("Validation unavailable")
                    if type(decisions) is not list or len(decisions) != len(calls):
                        raise HookError("Incomplete validation response")
                    refusal = None
                    # Check the entire owner's reply before using its first deny;
                    # a later malformed decision still means the validator failed.
                    for decision in decisions:
                        if decision == {"decision": "allow"}:
                            continue
                        if (type(decision) is not dict or set(decision) != {"decision", "message"}
                                or decision["decision"] != "deny"
                                or type(decision["message"]) is not str
                                or not 1 <= len(decision["message"]) <= 160
                                or decision["message"] != decision["message"].strip()
                                or not decision["message"].isprintable()):
                            raise HookError("Invalid validation decision")
                        refusal = refusal or decision["message"]
                    if refusal:
                        return codec.denied(refusal)
            finally:
                self._local.dispatching = False
                self._guest.release()
        except Exception as error:
            # Keep guards installed. Dropping required registrations here would
            # silently permit the very mutation whose validation just failed.
            for owner in affected:
                self.owners[owner] = "unavailable"
            self._report(error)
            return codec.denied("Validation unavailable")
        # Keep native execution outside adapter error handling. A native exception
        # must neither disable a healthy validator nor cause a second native call.
        return result if phase == "query" else original(*args, **kwargs)

    def poll(self):
        with self._lock:
            # Bridge pulses may arrive while an enrolled worker waits on a guest.
            # Report current readiness and defer mutations until the operation ends.
            if self._active:
                return self.status()
            super().poll()
            for owner in self.owners:
                if self.owners[owner] == "unavailable":
                    # Native binding readiness cannot heal a failed dispatcher;
                    # recovery requires explicit removal and fresh registration.
                    continue
                states = [self.groups[item.target]["state"] for key, item in self.entries.items()
                          if key[0] == owner]
                self.owners[owner] = ("unavailable" if "failed" in states else
                                      "ready" if all(state == "active" for state in states) else "pending")
            return self.status()

    def remove_owner(self, owner):
        """Disable an idle owner's bindings; active operations reject this change."""
        with self._lock:
            self._main()
            if owner not in self.owners:
                raise HookError("Unknown callable owner")
            removed = {item.target for key, item in self.entries.items() if key[0] == owner}
            self.entries = {key: item for key, item in self.entries.items() if key[0] != owner}
            del self.owners[owner]
            # Only targets with no remaining owner return to their native callable.
            for target in removed - {item.target for item in self.entries.values()}:
                super().remove(target)
                del self.groups[target]
            return self.status()

    def status(self):
        """Expose copied readiness/diagnostics, never projections or game references."""
        with self._lock:
            status = super().status()
            status["owners"] = dict(self.owners)
            return status

    def stop(self):
        """Restore all idle registrations; bridge callers use try_stop for busy work."""
        with self._lock:
            if not self.closed:
                self._main()
                for owner in list(self.owners):
                    self.remove_owner(owner)
                self._threads.clear()
            return super().stop()

    def try_stop(self):
        """Defer main-thread shutdown until native operations finish, without waiting.

        The bridge retries WAIT responses with its existing bounded request
        deadline. Check and stop under one lock so a worker cannot enter between.
        """
        with self._lock:
            if self.closed:
                return True
            # Retain the parent's main-thread check without our busy-error check:
            # an active operation must produce a retry rather than a fatal error.
            Manager._main(self)
            if self._active:
                return False
            self.stop()
            return True
