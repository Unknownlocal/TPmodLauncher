# SPDX-License-Identifier: MPL-2.0
"""A sealed owner policy view, with immutable shared-record reuse across updates."""

from .policy import fields
from .values import bounded, integer, loads, measure, text


class RegulationCatalog:
    def __init__(self, packet, bindings, previous):
        fields(packet, ("owner", "revision", "bindings", "rules", "choices", "records", "totals"))
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
        self.descriptors, self.records, self.by_id = [], {}, {}
        totals = [0, 0, 0]
        if type(packet["records"]) is not list:
            raise ValueError("Invalid resident record descriptors")
        integer(len(packet["records"]))
        ids = set()
        # Slots are local to this view; IDs identify records across generations.
        # Descriptor totals are recomputed before any new payload is accepted.
        for slot, descriptor in enumerate(packet["records"]):
            fields(descriptor, ("slot", "id", "kind", "bytes", "nodes", "depth", "integer_work"))
            if integer(descriptor["slot"]) != slot or descriptor["kind"] not in ("parameters", "option_data"):
                raise ValueError("Invalid resident record slot/type")
            identity = integer(descriptor["id"], 64, positive=True)
            if identity in ids:
                raise ValueError("Repeated resident record identity")
            ids.add(identity)
            metric = tuple(integer(descriptor[key], 63) for key in ("bytes", "nodes", "depth", "integer_work"))
            bounded(metric)
            if metric[0] < 2 or metric[1] < 1 or metric[2] < 1:
                raise ValueError("Invalid resident object measurements")
            self.descriptors.append((identity, descriptor["kind"], metric))
            totals = [integer(a + b, 63) for a, b in zip(totals, (metric[0], metric[1], metric[3]))]
            old = previous.by_id.get(identity) if previous is not None else None
            if old is not None:
                # The trusted publisher owns immutable IDs. Reuse the old value;
                # a changed kind or measurement under that ID is a protocol error.
                if old[:2] != (descriptor["kind"], metric):
                    raise ValueError("Resident record identity was changed")
                self.records[slot], self.by_id[identity] = old, old
        fields(packet["totals"], ("bytes", "nodes", "integer_work"))
        if totals != [integer(packet["totals"][key], 63) for key in ("bytes", "nodes", "integer_work")]:
            raise ValueError("Resident publication totals differ")
        choices = packet["choices"]
        if type(choices) is not list or not choices:
            raise ValueError("Missing resident policy choices")
        integer(len(choices))
        self.choices = []
        used, unique, covered = set(), set(), set()
        for row in choices:
            if type(row) is not list or len(row) != 4:
                raise ValueError("Invalid resident policy choice")
            rule, key, base, overlay = row
            rule, base, overlay = integer(rule), integer(base), integer(overlay)
            text(key, 128)
            if rule >= len(self.rules) or max(base, overlay) >= len(self.descriptors):
                raise ValueError("Foreign resident policy choice")
            if self.descriptors[base][1] != "parameters" or self.descriptors[overlay][1] != "option_data":
                raise ValueError("Resident record kinds differ")
            # Adopted choices can share a key while carrying different payloads.
            # Deduplicate the whole reference, preserving those distinct choices.
            choice = rule, key, base, overlay
            if choice in unique:
                raise ValueError("Repeated resident policy choice")
            unique.add(choice)
            used.update((base, overlay))
            covered.add(rule)
            self.choices.append(choice)
        # Every stored record must serve a choice, and every bound rule needs one.
        # This prevents unused data from inflating a supposedly filtered view.
        if used != set(range(len(self.descriptors))) or covered != set(range(len(self.rules))):
            raise ValueError("Unreachable or incomplete resident view")
        self.sealed = False

    def install(self, slot, value):
        slot = integer(slot)
        # Reused slots are already populated: neither a later page nor a sealed
        # publication may overwrite their canonical values.
        if self.sealed or slot >= len(self.descriptors) or slot in self.records or type(value) is not dict:
            raise ValueError("Repeated, foreign or invalid resident record")
        identity, kind, expected = self.descriptors[slot]
        metric = bounded(measure(value))
        if metric != expected:
            raise ValueError("Resident record measurements differ")
        record = kind, metric, value
        self.records[slot] = self.by_id[identity] = record

    def install_page(self, packet):
        rows = loads(packet)
        if type(rows) is not list or not rows:
            raise ValueError("Expected resident record page rows")
        # A bad row fails the whole resident operation at the export boundary;
        # earlier rows remain staged and cannot become a partial publication.
        for row in rows:
            if type(row) is not list or len(row) != 2:
                raise ValueError("Invalid resident record page row")
            self.install(*row)

    def seal(self):
        if self.sealed or len(self.records) != len(self.descriptors):
            raise ValueError("Repeated or incomplete resident policy seal")
        self.sealed = True
