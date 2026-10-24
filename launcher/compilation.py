# SPDX-License-Identifier: MPL-2.0
"""One compilation shared by preparation requests and later sandbox leases."""


class Compilation:
    def __init__(self, registry, key):
        self.registry, self.key = registry, key
        self.future = None
        # A preparation request becomes a sandbox lease at adoption, keeping
        # the same reference rather than briefly releasing and reacquiring code.
        self.users = 0
        self.code = None

    def release(self):
        self.registry.release(self)
