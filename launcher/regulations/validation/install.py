# SPDX-License-Identifier: MPL-2.0
"""Build one complete career install from shared JSON record fragments."""

from mod_runtime._regulations.policy import encode
from mod_runtime._regulations.values import install_fuel, measure
from mod_runtime.source_transfer import _chunks


class PolicyInstall:
    def __init__(self, views, epoch, generation):
        self.operations = []
        self.owners = len(views)
        self.records = tuple({record.identity: record for view in views.values() for record in view.records}.values())
        slots = {record.identity: index for index, record in enumerate(self.records)}
        value = {"owners": [view.manifest(slots) for view in views.values()],
                 "records": [dict(slot=index, id=record.identity, kind=record.kind,
                                  **dict(zip(("bytes", "nodes", "depth", "integer_work"), record.metric)))
                             for index, record in enumerate(self.records)],
                 "totals": {key: sum(record.metric[index] for record in self.records)
                            for key, index in (("bytes", 0), ("nodes", 1), ("integer_work", 3))}}
        text = encode(value)
        metric = self.metric = measure(value, encoded=text)
        self.operations.append(("begin-regulation-policy", (epoch, generation, metric[0]), "receiving"))
        self.operations.extend(("put-regulation-policy", (chunk,), None) for chunk in _chunks(text.encode("utf-8")))
        self.operations.append(("seal-regulation-policy-manifest", (), "manifest"))
        page, size = [], 2
        for slot, record in enumerate(self.records):
            row = "[" + str(slot) + "," + record.text + "]"
            row_size = len(row.encode("utf-8"))
            if page and size + row_size + 1 > 4096:
                self._page(page)
                page, size = [], 2
            if row_size + 2 > 4096:
                # A legal record can fill the scalar itself. Its page wrapper
                # would overflow, so transfer that one record without a wrapper.
                self.operations.append(("begin-regulation-record", (slot, record.metric[0]), None))
                self.operations.extend(("put-regulation-record", (chunk,), None)
                                       for chunk in _chunks(record.text.encode("utf-8")))
                self.operations.append(("seal-regulation-record", (), "records"))
            else:
                size += row_size + bool(page)
                page.append(row)
        if page:
            self._page(page)
        self.operations.append(("seal-regulation-policy", (), "sealed"))
        self.fuel = install_fuel(metric[0] + sum(record.metric[0] for record in self.records),
                                 metric[1] + sum(record.metric[1] for record in self.records),
                                 len(self.records), len(self.operations) + 1,
                                 metric[3] + sum(record.metric[3] for record in self.records)) + 8_000_000 * (self.owners - 1)
        # Keep each owner's installation base and the same total time ceiling.
        # Shared records are counted once; one commit reserves its crossing and
        # the owners' base work. Reset releases the previous career graph.
        self.commit_fuel = 1_000_000 * self.owners + 250_000

    def _page(self, rows):
        self.operations.append(("install-regulation-records", ("[" + ",".join(rows) + "]",), "records"))
