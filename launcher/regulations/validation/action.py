# SPDX-License-Identifier: MPL-2.0
"""Encode one proposal and give every mod references to the same inputs."""

from mod_runtime._regulations.policy import _integer, context_fields, encode, fields
from mod_runtime._regulations.values import array_measure, bounded, integer, measure, object_measure, scalar


class PreparedAction:
    def __init__(self, contexts, selections):
        if (type(contexts) is not list or len(contexts) != 1
                or type(selections) is not list or not 1 <= len(selections) <= 256):
            raise ValueError("Expected one shared proposal and bounded rule selections")
        context = contexts[0]
        context_fields(context)
        encoded = encode(context)
        self.context = context, encoded, bounded(measure(context, encoded=encoded))
        self.selections, self.used_selections = selections, set()

    def _selection(self, view, selection, revision):
        fields(selection, ("rule", "evaluation_season", "selected", "current", "upcoming"))
        rule, season = selection["rule"], selection["evaluation_season"]
        _integer(season)
        if rule not in view.rule_slots or selection["selected"] not in ("current", "upcoming"):
            raise ValueError("Foreign resident selection")
        wire = {"rule": view.rule_slots[rule], "evaluation_season": season, "selected": selection["selected"]}
        metrics = [("schema", scalar(1)), ("key", scalar(rule)), ("revision", scalar(revision)),
                   ("evaluation_season", scalar(season)), ("selected", scalar(selection["selected"]))]
        for name in ("current", "upcoming"):
            reference = selection[name]
            if name == "upcoming" and reference is None:
                # Current is mandatory. Season/selection semantics come from the
                # native mapper; here we only resolve its authorized references.
                wire[name], metric = None, scalar(None)
            else:
                if type(reference) is not list or len(reference) != 2:
                    raise ValueError("Invalid resident choice reference")
                choice = view.references[rule, integer(reference[0])]
                _integer(reference[1])
                _, key, base, overlay = view.choices[choice]
                wire[name] = [choice, reference[1]]
                metric = object_measure((("key", scalar(key)), ("effective_season", scalar(reference[1])),
                                         ("parameters", base.metric), ("option_data", overlay.metric)))
            metrics.append((name, metric))
        if wire[wire["selected"]] is None:
            raise ValueError("Missing selected resident choice")
        encoded = encode(wire)
        return encoded, measure(wire, encoded=encoded), object_measure(metrics), rule, season

    def for_owner(self, view, calls, revision):
        """Build one mod's call packet referencing the shared proposal."""
        if type(calls) is not list or not 1 <= len(calls) <= 32:
            raise ValueError("Invalid resident owner call count")
        selections, rows = {}, []
        previous = -1
        for row in calls:
            if type(row) is not list or len(row) != 3:
                raise ValueError("Invalid resident owner call")
            validator, context, selection = row
            binding, declaration = view.validators[validator]
            context, selection = integer(context), integer(selection)
            # A declaration runs once per proposal, in admitted validator order.
            if binding <= previous or context != 0 or selection >= len(self.selections):
                raise ValueError("Unknown or out-of-order resident invocation")
            if selection not in selections:
                # Validators of the same rule share this small descriptor;
                # each handler still receives fresh argument copies.
                selections[selection] = len(selections), self._selection(view, self.selections[selection], revision)
            _, (_, _, rule_metric, rule, season) = selections[selection]
            value, _, metric = self.context
            if value["scope"] != declaration["scope"] or rule != declaration["rule"] or value["effective"]["season"] != season:
                raise ValueError("Foreign resident invocation")
            # Check the full argument size using cached counts, without walking
            # the rule values again for every call.
            bounded(object_measure((("context", metric), ("regulation", rule_metric))))
            # Every owner references the same single proposal in slot zero.
            rows.append([binding, 0, selections[selection][0]])
            self.used_selections.add(selection)
            previous = binding
        calls_text = encode(rows)
        parts = (("owner", encode(view.owner), scalar(view.owner)),
                 ("revision", encode(view.revision), scalar(view.revision)),
                 ("contexts", "[0]", array_measure((scalar(0),))),
                 ("selections", "[" + ",".join(value[0] for _, value in selections.values()) + "]",
                  array_measure(value[1] for _, value in selections.values())),
                 ("calls", calls_text, measure(rows, encoded=calls_text)))
        packet = "{" + ",".join(encode(key) + ":" + text for key, text, _ in parts) + "}"
        metric = object_measure((key, metric) for key, _, metric in parts)
        if metric[0] > 384 * 1024:
            raise ValueError("Resident owner packet exceeds its bound")
        return packet, metric, len(rows)

    def for_group(self):
        """Wrap the cached proposal for the shared runner."""
        _, text, metric = self.context
        return "[" + text + "]", array_measure((metric,)), 1

    def seal(self):
        # Every selection must reach a handler. Each validated call already uses
        # the single shared proposal, so no context-reference bookkeeping remains.
        if len(self.used_selections) != len(self.selections):
            raise ValueError("Unreferenced resident action data")
