# SPDX-License-Identifier: MPL-2.0
"""Keep one mod's bindings and choices as references into the shared record table."""

from .policy import fields
from .values import integer, text


class RegulationView:
    def __init__(self, packet, bindings, catalog):
        fields(packet, ("owner", "revision", "bindings", "rules", "choices"))
        self.identity = text(packet["owner"], 128), text(packet["revision"], 64)
        if len(self.identity[1]) != 64 or any(c not in "0123456789abcdef" for c in self.identity[1]):
            raise ValueError("Expected the admitted owner revision")
        rules = packet["rules"]
        if type(rules) is not list or not 1 <= len(rules) <= 32:
            raise ValueError("Invalid resident rules")
        self.rules = tuple(text(rule, 256) for rule in rules)
        if len(set(self.rules)) != len(self.rules) or type(packet["bindings"]) is not list:
            raise ValueError("Duplicate resident rules or invalid bindings")
        if len(packet["bindings"]) != len(bindings):
            raise ValueError("Resident bindings differ from admitted handlers")
        self.bindings = tuple(bindings)
        # Manifest slots refer to the already admitted functions in this order.
        # Matching scope/rule prevents a publication from redirecting a handler.
        for row, (_, scope, rule) in zip(packet["bindings"], self.bindings):
            fields(row, ("rule", "scope"))
            slot = integer(row["rule"])
            if slot >= len(self.rules) or self.rules[slot] != rule or row["scope"] != scope:
                raise ValueError("Foreign resident binding")
        if set(self.rules) != {rule for _, _, rule in bindings}:
            raise ValueError("Unreferenced resident rule")
        choices = packet["choices"]
        if type(choices) is not list or not choices:
            raise ValueError("Missing resident policy choices")
        integer(len(choices))
        self.choices = []
        unique, covered = set(), set()
        for row in choices:
            if type(row) is not list or len(row) != 4:
                raise ValueError("Invalid resident policy choice")
            rule, key, base, overlay = row
            rule, base, overlay = integer(rule), integer(base), integer(overlay)
            text(key, 128)
            if rule >= len(self.rules) or max(base, overlay) >= len(catalog.descriptors):
                raise ValueError("Foreign resident policy choice")
            if catalog.descriptors[base][1] != "parameters" or catalog.descriptors[overlay][1] != "option_data":
                raise ValueError("Resident record kinds differ")
            # Adopted choices can share a key while carrying different payloads.
            # Deduplicate the whole reference, preserving those distinct choices.
            choice = rule, key, base, overlay
            if choice in unique:
                raise ValueError("Repeated resident policy choice")
            unique.add(choice)
            covered.add(rule)
            self.choices.append(choice)
        if covered != set(range(len(self.rules))):
            raise ValueError("Incomplete resident rule choices")
        self.records = catalog.records
