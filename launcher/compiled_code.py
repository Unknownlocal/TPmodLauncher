# SPDX-License-Identifier: MPL-2.0
"""Share compiled code while each mod keeps its own Store and permissions.

Wasmtime's compiled Python runner is much larger than an individual mod's source.
Keep one copy per portable component and execution thread until its last request
or sandbox releases it. Engine-wide watchdogs are safe only when one mod owns a
complete phase; the owner check also covers host work between individual calls.
"""

import threading

from launcher.deadline_watchdog import DeadlineWatchdog


class CompiledCode:
    def __init__(self, engine, component, resources, thread):
        self.engine, self.component, self.resources = engine, component, resources
        self.thread = thread
        self.closed, self.owner, self.depth = False, None, 0
        self.watchdog = DeadlineWatchdog(engine)
        # Close the watchdog before the Component and Engine already in this stack.
        self.resources.callback(self.watchdog.close)

    def close(self):
        """Free native code after the registry's last request or sandbox releases it."""
        self.closed = True
        self.resources.close()

    def claim(self, owner):
        """Keep another Store out while this owner's watchdog is armed."""
        if threading.get_ident() != self.thread or self.closed:
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
