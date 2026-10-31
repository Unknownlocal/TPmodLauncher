# SPDX-License-Identifier: MPL-2.0
"""Validate and own one career's physical records and per-mod choice views.

Record descriptions and FrozenValue payloads are stored once for the shared
interpreter. Owner views only refer to their slots; handlers receive fresh copies.
"""

import gc

from .policy import fields
from .frozen import FrozenValue
from .values import bounded, integer, loads, measure
from .view import RegulationView


class RegulationCatalog:
    def __init__(self, packet, bindings):
        fields(packet, ("owners", "records", "totals"))
        self.descriptors, self.records = [], {}
        totals = [0, 0, 0]
        if type(packet["records"]) is not list:
            raise ValueError("Invalid resident record descriptors")
        integer(len(packet["records"]))
        ids = set()
        # Slots address the shared table; IDs identify records across careers.
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
        fields(packet["totals"], ("bytes", "nodes", "integer_work"))
        if totals != [integer(packet["totals"][key], 63) for key in ("bytes", "nodes", "integer_work")]:
            raise ValueError("Resident publication totals differ")
        owners = packet["owners"]
        if type(owners) is not list or len(owners) != len(bindings):
            raise ValueError("Incomplete shared regulation owners")
        self.views = {}
        used = set()
        for row in owners:
            if type(row) is not dict or type(row.get("owner")) is not str:
                raise ValueError("Invalid shared regulation owner")
            owner = row["owner"]
            if owner not in bindings or owner in self.views:
                raise ValueError("Foreign or repeated shared regulation owner")
            view = RegulationView(row, bindings[owner], self)
            self.views[owner] = view
            used.update(slot for _, _, base, overlay in view.choices for slot in (base, overlay))
        # Reject extra records as well as missing owner/rule references. All
        # transferred data must belong to this complete admitted installation.
        if used != set(range(len(self.descriptors))):
            raise ValueError("Unreachable shared regulation records")
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
        # Keep the checked value in the flat cache format. Validation creates
        # ordinary argument copies from it rather than passing cached data out.
        record = kind, metric, FrozenValue(value)
        self.records[slot] = record

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
        # Collect recently created objects under the installation allowance so
        # Python can stop tracking flat cached tuples before validators run.
        # A full collection would also scan older mod objects and clear pools
        # of reusable storage. Automatic collection for mod objects stays enabled.
        gc.collect(0)
