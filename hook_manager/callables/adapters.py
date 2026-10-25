# SPDX-License-Identifier: MPL-2.0
"""Own private native wrappers using the existing hook lifecycle."""

from contextlib import contextmanager
import math
import threading
from weakref import WeakSet

# Normal imports resolve these dependencies; the fixed game bundle supplies them
# earlier in its shared private namespace, where __package__ is None.
if __package__:
    from ..manager import HookError, Manager, identifier
    from .binding import CallBinding
    from .target import CallableTarget


class CallableAdapters(Manager):
    """Share one owned native wrapper between required validator owners.

    Reuse Manager's deferred imports, main-thread installation and restoration
    journal. Callback failures latch owners unavailable while retaining guards;
    explicit owner removal is the only path back to unguarded native execution.
    No request/bridge operation can approve targets or register bindings.

    A trusted subclass supplies validation; this base only owns native wrappers
    and their lifecycle. There is no generic dispatcher or public call operation.
    """

    _validate = None

    def __init__(self, *, seconds=.1):
        # The parent callback path is unused; our wrapper enters the subclass's
        # validation before native execution instead of dispatching legacy hooks.
        super().__init__(lambda record: None)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
            raise HookError("Expected a positive finite validation deadline")
        self.seconds = seconds
        self.entries, self.owners = {}, {}
        # Registry changes and guest waiting use separate locks. Status and
        # cleanup can re-enter the registry without waiting behind guest work.
        self._lock, self._guest = threading.RLock(), threading.Lock()
        self._local, self._threads = threading.local(), WeakSet()
        # Pin configuration for both guarded calls and whole worker scopes.
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
        if owner in self.owners or not callable(self._validate):
            raise HookError("Owner already registered or no trusted validation adapter configured")
        if (type(bindings) is not list or not 1 <= len(bindings) <= 32
                or any(not isinstance(item, CallBinding) for item in bindings)
                or len({item.id for item in bindings}) != len(bindings)
                or len(self.entries) + len(bindings) > 256):
            raise HookError("Invalid or oversized owned callable group")
        # Copy descriptors so later caller edits cannot change installed
        # projections or refusals. Functions remain trusted shared references.
        staged = []
        for item in bindings:
            if item.target not in self.targets:
                raise HookError("Target has not been approved by the launcher")
            binding = CallBinding(item.id, item.target, item.project, item.refuse)
            for existing in [*self.entries.values(), *staged]:
                if existing.target == binding.target and existing.refuse is not binding.refuse:
                    raise HookError("Owners disagree on the native refusal format")
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
                    # wrapper enters the trusted adapter validation path.
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
