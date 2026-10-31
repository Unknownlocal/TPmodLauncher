# SPDX-License-Identifier: MPL-2.0
"""Copy market dictionaries/lists together and publish without losing their aliases."""


class MarketGraph:
    """Copy native dictionaries/lists while keeping their shared values shared."""

    def __init__(self):
        self._copies = {}
        self._originals = {}

    def copy(self, value):
        """Copy mutable market containers once; borrow other native values."""
        if not isinstance(value, (dict, list)):
            return value
        # Nested callers already use the private view. Copying it again would
        # split shared offers from the context dictionaries that refer to them.
        if id(value) in self._originals:
            return value
        if id(value) in self._copies:
            return self._copies[id(value)]
        private = {} if isinstance(value, dict) else []
        # Register before descending: repeated references and cycles must point
        # to the same private container, just as they do in the live market.
        self._copies[id(value)] = private
        self._originals[id(private)] = value
        if isinstance(value, dict):
            private.update((key, self.copy(item)) for key, item in value.items())
        else:
            private.extend(self.copy(item) for item in value)
        return private

    def private(self, value):
        return self._copies.get(id(value), value)

    def original(self, value):
        return self._originals.get(id(value))

    def publish(self, values):
        """Update the game's existing containers without exposing private ones."""
        # One memo covers all selected stores and mail in this publication.
        memo = {}

        def publish(value):
            if not isinstance(value, (dict, list)):
                return value
            if id(value) in memo:
                return memo[id(value)]
            target = self._originals.get(id(value))
            if target is None:
                # Native code may have created a new row/list while preparing.
                # Give it a separate live container so later private writes
                # cannot leak through a previously published reference.
                target = {} if isinstance(value, dict) else []
                self._originals[id(value)] = target
                self._copies[id(target)] = value
            memo[id(value)] = target
            if isinstance(value, dict):
                prepared = {key: publish(item) for key, item in value.items()}
                # Keep dictionary identity: other game code can still hold it.
                target.clear()
                target.update(prepared)
            else:
                target[:] = [publish(item) for item in value]
            return target

        return {name: publish(value) for name, value in values.items()}
