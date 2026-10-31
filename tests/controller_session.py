# SPDX-License-Identifier: MPL-2.0
"""Model the Frida session events used by controller tests."""


class ControllerSession:
    # This fake models Frida's event contract; tests choose when native events arrive.
    def __init__(self, device, pid):
        self.device = device
        self.pid = pid
        self.handlers = {}
        self.is_detached = False
        self.gating = False

    def on(self, signal, callback):
        self.handlers[signal] = callback

    def enable_child_gating(self):
        self.gating = True
        self.device.calls.append(("gate", self.pid))
        if self.device.failure == "gate":
            raise RuntimeError("gating failed")

    def disable_child_gating(self):
        self.gating = False
        self.device.calls.append(("ungate", self.pid))
        # Deliver a late child during cleanup to exercise the queued-callback path.
        if self.device.late is not None:
            child, self.device.late = self.device.late, None
            self.device.pending[child.pid] = child
            self.device.handlers["child-added"](child)

    def detach(self):
        self.device.calls.append(("detach", self.pid))
        self.is_detached = True
        self.handlers["detached"]("application-requested")
