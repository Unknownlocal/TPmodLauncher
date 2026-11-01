# SPDX-License-Identifier: MPL-2.0
"""Model a pinned process-exit event independently of Frida detach delivery."""


class ControllerExit:
    def __init__(self, device, pid, executable, completed):
        self.completed = completed
        self.closed = False
        self.exited = False
        device.exit_waits[pid] = self

    def start(self):
        pass

    def close(self):
        self.closed = True

    def has_exited(self):
        return self.exited
