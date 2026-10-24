# SPDX-License-Identifier: MPL-2.0
"""Check one action and prepare separate arguments for its validator calls.

Resolve rule references against the installed catalog instead of receiving full
rule values again. Prepare at most four argument pairs at a time to limit temporary
memory. Copying happens outside each handler's allowance; mods can keep or change
their arguments without affecting later calls or cached rules.
"""

from .policy import _integer, fields
from .values import bounded, clone, integer, object_measure, scalar


class RegulationAction:
    def __init__(self, packet, catalog, revision, *, contexts):
        fields(packet, ("owner", "revision", "contexts", "selections", "calls"))
        if (packet["owner"], packet["revision"]) != catalog.identity:
            raise ValueError("Foreign resident action owner/revision")
        for key in ("contexts", "selections", "calls"):
            if type(packet[key]) is not list or not 1 <= len(packet[key]) <= 32:
                raise ValueError("Invalid resident action dimensions")
        self.contexts, context_metrics, metadata = [], [], []
        # The group has already checked and measured the proposals. Resolve each
        # index here, then check the full context-plus-rule size below.
        for context in packet["contexts"]:
            value, metric, scope, season = contexts.get(context)
            self.contexts.append(value)
            context_metrics.append(metric)
            metadata.append((scope, season))
        if len(set(packet["contexts"])) != len(self.contexts):
            raise ValueError("Repeated shared regulation context reference")
        self.rules, rule_metrics = [], []
        for selection in packet["selections"]:
            fields(selection, ("rule", "evaluation_season", "selected", "current", "upcoming"))
            slot = integer(selection["rule"])
            if slot >= len(catalog.rules):
                raise ValueError("Foreign resident rule")
            _integer(selection["evaluation_season"])
            if selection["selected"] not in ("current", "upcoming"):
                raise ValueError("Invalid selected resident option")
            rule = {"schema": 1, "key": catalog.rules[slot], "revision": revision,
                    "evaluation_season": selection["evaluation_season"], "selected": selection["selected"]}
            metrics = [(key, scalar(value)) for key, value in rule.items()]
            for name in ("current", "upcoming"):
                reference = selection[name]
                if name == "upcoming" and reference is None:
                    # A current choice is mandatory; only upcoming can be absent.
                    rule[name], metric = None, scalar(None)
                else:
                    if type(reference) is not list or len(reference) != 2:
                        raise ValueError("Invalid resident choice reference")
                    choice_slot = integer(reference[0])
                    _integer(reference[1])
                    if choice_slot >= len(catalog.choices):
                        raise ValueError("Unknown resident choice")
                    choice_rule, key, base, overlay = catalog.choices[choice_slot]
                    # A valid slot from another rule is still an invalid input
                    # for this selection. The trusted caller supplies its season.
                    if choice_rule != slot:
                        raise ValueError("Resident choice belongs to another rule")
                    parameters, option_data = catalog.records[base], catalog.records[overlay]
                    rule[name] = {"key": key, "effective_season": reference[1],
                                  "parameters": parameters[2], "option_data": option_data[2]}
                    metric = object_measure((("key", scalar(key)), ("effective_season", scalar(reference[1])),
                                             ("parameters", parameters[1]), ("option_data", option_data[1])))
                metrics.append((name, metric))
            if rule[rule["selected"]] is None:
                raise ValueError("Missing selected resident choice")
            self.rules.append(rule)
            rule_metrics.append(object_measure(metrics))
        self.calls = []
        self.context_uses = [0] * len(self.contexts)
        self.rule_uses = [0] * len(self.rules)
        previous = -1
        for row in packet["calls"]:
            if type(row) is not list or len(row) != 3:
                raise ValueError("Invalid resident call row")
            binding, context, selection = (integer(value) for value in row)
            # Repeated calls to one binding are allowed; moving backwards would
            # change the admitted validator order.
            if (binding < previous or binding >= len(catalog.bindings)
                    or context >= len(self.contexts) or selection >= len(self.rules)):
                raise ValueError("Unknown or out-of-order resident call")
            function, scope, rule = catalog.bindings[binding]
            if (metadata[context][0] != scope or self.rules[selection]["key"] != rule
                    or metadata[context][1] != self.rules[selection]["evaluation_season"]):
                raise ValueError("Foreign resident invocation")
            # References save wire work, but all referenced payloads still count
            # toward the ordinary logical invocation limit.
            bounded(object_measure((("context", context_metrics[context]), ("regulation", rule_metrics[selection]))))
            self.calls.append((function, context, selection))
            self.context_uses[context] += 1
            self.rule_uses[selection] += 1
            previous = binding
        # Positive usage counts also prove that no unused table row was supplied.
        if not all(self.context_uses) or not all(self.rule_uses):
            raise ValueError("Unreferenced resident action data")
        self.offset, self.prepared, self.next = 0, [], 0
        self.prepare()

    def prepare(self):
        if self.prepared or self.offset == len(self.calls):
            raise ValueError("Unconsumed or finished resident action")
        end = min(self.offset + 4, len(self.calls))
        # Each call gets new containers, including repeated selections. An author
        # may retain old arguments and mutate them during a later call.
        self.prepared = [(function, clone(self.contexts[context]), clone(self.rules[selection]))
                         for function, context, selection in self.calls[self.offset:end]]
        # Retire canonical action data at its last preparation use. Deferring a
        # whole 32-context graph's deallocation to the last validate export would
        # spend framework cleanup from that handler's ordinary 5M allowance.
        for _, context, selection in self.calls[self.offset:end]:
            self.context_uses[context] -= 1
            self.rule_uses[selection] -= 1
            if self.context_uses[context] == 0:
                self.contexts[context] = None
            if self.rule_uses[selection] == 0:
                self.rules[selection] = None
        self.offset, self.next = end, 0
        return len(self.prepared)

    def take(self, slot):
        # Slots restart at zero for each prepared group. Consume before invoking
        # author code so the same arguments cannot be taken a second time.
        if integer(slot) != self.next or not self.prepared:
            raise ValueError("Repeated or out-of-order resident slot")
        result = self.prepared[slot]
        self.prepared[slot] = None
        self.next += 1
        if self.next == len(self.prepared):
            self.prepared = []
        return result

    @property
    def finished(self):
        return self.offset == len(self.calls) and not self.prepared
