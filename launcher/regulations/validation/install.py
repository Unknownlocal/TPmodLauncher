# SPDX-License-Identifier: MPL-2.0
"""Finite owner installation plan assembled from cached JSON record fragments."""

from mod_runtime._regulations.values import install_fuel
from mod_runtime.runtime import _chunks


class PolicyInstall:
    def __init__(self, view, previous, epoch, generation, *, known=None):
        self.operations = []
        if view is previous:
            self.operations = [("begin-regulation-policy", (epoch, generation, 0), "manifest"),
                               ("seal-regulation-policy", (), "sealed")]
            self.fuel = install_fuel(0, 0, 0, 3, 0)
            self.commit_fuel = 1_250_000
            return
        text, metric = view.manifest()
        if known is None:
            known = {record.identity for record in previous.records} if previous is not None else set()
        # A shared publisher carries the union of installed/staged record IDs.
        # Another owner then borrows the native record instead of sending it again.
        incoming = [(slot, record) for slot, record in enumerate(view.records) if record.identity not in known]
        self.operations.append(("begin-regulation-policy", (epoch, generation, metric[0]), "receiving"))
        self.operations.extend(("put-regulation-policy", (chunk,), None) for chunk in _chunks(text.encode("utf-8")))
        self.operations.append(("seal-regulation-policy-manifest", (), "manifest"))
        page, size = [], 2
        for slot, record in incoming:
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
        retired = previous.retirement_nodes(view) if previous is not None else 0
        self.fuel = install_fuel(metric[0] + sum(record.metric[0] for _, record in incoming),
                                 metric[1] + retired + sum(record.metric[1] for _, record in incoming),
                                 len(view.records), len(self.operations) + 1,
                                 metric[3] + sum(record.metric[3] for _, record in incoming))
        # Reserve part of the same allowance for commit: replacing active can
        # release a large old graph, even after all new records have been staged.
        # 1M comes from the formula's 8M base; 250k is the commit crossing. The
        # retirement-node allowance moves here too, rather than being added twice.
        self.commit_fuel = 1_250_000 + 64_000 * retired

    def _page(self, rows):
        self.operations.append(("install-regulation-records", ("[" + ",".join(rows) + "]",), "records"))
