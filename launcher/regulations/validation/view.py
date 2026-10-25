# SPDX-License-Identifier: MPL-2.0
"""Select the cached rules and private data that one mod is allowed to receive.

Refer to shared CatalogRecords instead of duplicating their values. Build the
sandbox's setup table once, using record numbers to avoid embedding the same
payload repeatedly. Private option data remains specific to its owning mod.
"""

from mod_runtime._regulations.policy import encode
from mod_runtime._regulations.values import integer, measure


class OwnerView:
    def __init__(self, owner, revision, validators, choices):
        self.owner, self.revision = owner, revision
        self.rules = tuple(sorted({row["rule"] for row in validators}))
        self.rule_slots = {rule: index for index, rule in enumerate(self.rules)}
        self.validators = {row["id"]: (index, {key: row[key] for key in ("rule", "scope", "handler")})
                           for index, row in enumerate(validators)}
        self.choices, self.references, records = [], {}, {}
        for rule in self.rules:
            for index, (key, base, overlay) in enumerate(choices[rule]):
                self.references[rule, index] = len(self.choices)
                self.choices.append((rule, key, base, overlay))
                records[base.identity], records[overlay.identity] = base, overlay
        self.records = tuple(records.values())
        self._manifest = None

    def manifest(self):
        if self._manifest is None:
            # Only the owner-specific indexes are encoded here. Record payload
            # fragments remain shared and are never embedded in the manifest.
            slots = {record.identity: index for index, record in enumerate(self.records)}
            integer(len(self.choices))
            integer(len(slots))
            value = {"owner": self.owner, "revision": self.revision, "rules": list(self.rules),
                     "bindings": [{"rule": self.rule_slots[row["rule"]], "scope": row["scope"]}
                                  for _, row in self.validators.values()],
                     "choices": [[self.rule_slots[rule], key, slots[base.identity], slots[overlay.identity]]
                                 for rule, key, base, overlay in self.choices],
                     "records": [dict(slot=index, id=record.identity, kind=record.kind,
                                      **dict(zip(("bytes", "nodes", "depth", "integer_work"), record.metric)))
                                 for index, record in enumerate(self.records)],
                     "totals": {key: sum(record.metric[index] for record in self.records)
                                for key, index in (("bytes", 0), ("nodes", 1), ("integer_work", 3))}}
            text = encode(value)
            metric = measure(value, encoded=text)
            integer(metric[0], positive=True)
            self._manifest = text, metric
        return self._manifest
