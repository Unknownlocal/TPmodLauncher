# SPDX-License-Identifier: MPL-2.0
"""Share compiled code while each mod keeps its own Store and permissions.

Wasmtime's compiled Python runner is much larger than an individual mod's source.
Keep one copy per portable component and execution thread until its last sandbox
closes. Engine-wide watchdogs are safe only when one mod owns a complete phase;
the owner check also covers host work between individual guest calls.
"""

from contextlib import ExitStack
import hashlib
import threading
import weakref

from launcher.deadline_watchdog import DeadlineWatchdog


class CompiledCode:
    _local = threading.local()

    def __init__(self, engine, component, resources, cache, key):
        self.engine, self.component, self.resources = engine, component, resources
        self.cache, self.key = cache, key
        self.thread = threading.get_ident()
        self.users, self.owner, self.depth = 0, None, 0
        self.watchdog = DeadlineWatchdog(engine)
        # Close the watchdog before the Component and Engine already in this stack.
        self.resources.callback(self.watchdog.close)

    @classmethod
    def adopt(cls, data, engine, component, resources):
        """Transfer a compiler result or discard its duplicate, returning one lease."""
        # Preparation can run elsewhere. Choose sharing only when the Store's
        # execution thread adopts the result, never on the compiler's thread.
        if not hasattr(cls._local, "cache"):
            cls._local.cache = weakref.WeakValueDictionary()
        cache = cls._local.cache
        # This identifies immutable portable code at admission, not game-method
        # compatibility. Retaining the full runner bytes as a key costs megabytes.
        key = hashlib.sha256(data).digest()
        code = cache.get(key)
        if code is None:
            code = cls(engine, component, resources.pop_all(), cache, key)
            cache[key] = code
        else:
            resources.close()
        code.users += 1
        lease = ExitStack()
        lease.callback(code.release)
        return code, lease

    def release(self):
        """Free code deterministically when the last sandbox releases its lease."""
        self.users -= 1
        if not self.users:
            self.cache.pop(self.key)
            self.cache = self.key = None
            self.resources.close()

    def claim(self, owner):
        """Keep another Store out while this owner's watchdog is armed."""
        if threading.get_ident() != self.thread or not self.users:
            raise RuntimeError("Compiled code requires its owning execution thread")
        if self.owner is not None and self.owner is not owner:
            raise RuntimeError("Another sandbox owns this compiled code's execution phase")
        self.owner, self.depth = owner, self.depth + 1

    def unclaim(self, owner):
        # A call inside its owner's phase temporarily adds a second claim.
        assert self.owner is owner and self.depth > 0
        self.depth -= 1
        if not self.depth:
            self.owner = None
