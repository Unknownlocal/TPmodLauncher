# SPDX-License-Identifier: MPL-2.0
"""Finite policy ingestion on the controller, with page-by-page backpressure."""

from hook_manager.game_adapters.regulations.protocol import regulation_integer, regulation_policy_value


class PolicyPublication:
    def __init__(self, dispatch, message):
        plan = message["plan"]
        # Switch-only policies have no option records. Their empty publication
        # still needs the normal begin/seal acknowledgement for this career.
        if type(plan) is not list or len(plan) != len(dispatch.catalog.rules):
            raise ValueError("Invalid regulation publication plan")
        self.native, self.custom, self._expected = {}, {}, []
        seen = set()
        for row in plan:
            if type(row) is not list or len(row) != 5:
                raise ValueError("Invalid regulation membership plan")
            kind, rule, count, size, nodes = row
            if kind not in ("native", "custom") or type(rule) is not str or rule in seen:
                raise ValueError("Repeated or foreign regulation membership")
            seen.add(rule)
            mapping = self.native if kind == "native" else self.custom
            for number in (size, nodes):
                regulation_integer(number)
            # Counts are declarations, not allocation sizes. Pages verify each
            # record and the exact totals before any guest install can begin.
            if type(count) is not int or not 1 <= count < 2**32 or not count <= nodes <= 1024 * count or not count <= size <= 8192 * count:
                raise ValueError("Invalid finite regulation record counts")
            mapping[rule] = []
            self._expected.append([kind, rule, count, size, nodes])
        dispatch.catalog.check_publication(self.native, self.custom)
        generation = dispatch.generation + 1
        if message["generations"] != dict.fromkeys(dispatch.catalog.owners, generation):
            raise ValueError("Foreign regulation publication generations")
        self.identity = {key: message[key] for key in ("id", "epoch", "deadline", "generations")}
        # Page numbering spans this publication, rather than restarting per rule.
        # The rule cursor and its byte/node totals persist until its count is filled.
        self.page, self._rule, self._size, self._nodes = 0, 0, 0, 0

    def check(self, message):
        if any(message[key] != value for key, value in self.identity.items()):
            raise ValueError("Foreign regulation publication phase")

    def append(self, message):
        self.check(message)
        if type(message["page"]) is not int or message["page"] != self.page or type(message["rows"]) is not list or not message["rows"]:
            raise ValueError("Repeated or empty regulation page")
        for row in message["rows"]:
            if type(row) is not list or len(row) != 3 or self._rule >= len(self._expected):
                raise ValueError("Excess regulation policy record")
            kind, rule, count, size, nodes = self._expected[self._rule]
            values = (self.native if kind == "native" else self.custom)[rule]
            # Choice order defines later action indexes, including distinct
            # saved payloads sharing a key. Never reorder or merge these rows.
            if row[0] != rule or type(row[1]) is not int or row[1] != len(values):
                raise ValueError("Foreign or reordered regulation choice")
            value, actual_bytes, actual_nodes = regulation_policy_value(kind, row[2])
            self._size += actual_bytes
            self._nodes += actual_nodes
            if self._size > size or self._nodes > nodes:
                raise ValueError("Regulation publication exceeds its declared plan")
            values.append(value)
            if len(values) == count:
                # Reaching the record count is insufficient if the sender's
                # declared bytes/nodes differ. Do not advance to the next rule.
                if (self._size, self._nodes) != (size, nodes):
                    raise ValueError("Incomplete regulation record metrics")
                self._rule += 1
                self._size = self._nodes = 0
        self.page += 1

    def seal(self, dispatch, message):
        self.check(message)
        if self._rule != len(self._expected):
            raise ValueError("Incomplete regulation membership")
        # No guest install starts until every declared membership is complete;
        # time already spent receiving pages remains charged to this deadline.
        return dispatch.publish(native=self.native, custom=self.custom, deadline=self.identity["deadline"])
