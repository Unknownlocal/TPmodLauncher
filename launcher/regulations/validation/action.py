# SPDX-License-Identifier: MPL-2.0
"""Once-per-action contexts and checked owner-local selection/call references."""

from mod_runtime._regulations.policy import _integer, context_fields, encode, fields
from mod_runtime._regulations.values import array_measure, bounded, integer, measure, object_measure, scalar


class PreparedAction:
    def __init__(self, contexts, selections):
        if any(type(rows) is not list or not 1 <= len(rows) <= 1024 for rows in (contexts, selections)):
            raise ValueError("Invalid resident action tables")
        self.contexts, self.selections = [], selections
        self.used_contexts, self.used_selections = set(), set()
        for context in contexts:
            context_fields(context)
            encoded = encode(context)
            self.contexts.append((context, encoded, bounded(measure(context, encoded=encoded))))

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

    def for_owner(self, view, calls, revision, *, shared=None):
        if type(calls) is not list or not 1 <= len(calls) <= 32:
            raise ValueError("Invalid resident owner call count")
        contexts, selections, rows = {}, {}, []
        previous = -1
        for row in calls:
            if type(row) is not list or len(row) != 3:
                raise ValueError("Invalid resident owner call")
            validator, context, selection = row
            binding, declaration = view.validators[validator]
            context, selection = integer(context), integer(selection)
            if binding < previous or context >= len(self.contexts) or selection >= len(self.selections):
                raise ValueError("Unknown or out-of-order resident invocation")
            if selection not in selections:
                # Repeated calls share one small owner-local selection descriptor;
                # each Store still receives its own fresh handler argument copies.
                selections[selection] = len(selections), self._selection(view, self.selections[selection], revision)
            _, (_, _, rule_metric, rule, season) = selections[selection]
            value, text, metric = self.contexts[context]
            if value["scope"] != declaration["scope"] or rule != declaration["rule"] or value["effective"]["season"] != season:
                raise ValueError("Foreign resident invocation")
            # Full referenced payload metrics count without visiting their values.
            bounded(object_measure((("context", metric), ("regulation", rule_metric))))
            if context not in contexts:
                # Keep the encoding made at aggregate entry. Owner filtering
                # changes local indexes, without re-encoding the context itself.
                if shared is not None:
                    # One group table owns the original encoded proposal. Owner
                    # envelopes carry only its number, with their own local slots.
                    index = shared.setdefault(context, len(shared))
                    text, metric = encode(index), scalar(index)
                contexts[context] = len(contexts), text, metric
            rows.append([binding, contexts[context][0], selections[selection][0]])
            self.used_contexts.add(context)
            self.used_selections.add(selection)
            previous = binding
        calls_text = encode(rows)
        parts = (("owner", encode(view.owner), scalar(view.owner)),
                 ("revision", encode(view.revision), scalar(view.revision)),
                 ("contexts", "[" + ",".join(text for _, text, _ in contexts.values()) + "]",
                  array_measure(metric for _, _, metric in contexts.values())),
                 ("selections", "[" + ",".join(value[0] for _, value in selections.values()) + "]",
                  array_measure(value[1] for _, value in selections.values())),
                 ("calls", calls_text, measure(rows, encoded=calls_text)))
        packet = "{" + ",".join(encode(key) + ":" + text for key, text, _ in parts) + "}"
        metric = object_measure((key, metric) for key, _, metric in parts)
        if metric[0] > 384 * 1024:
            raise ValueError("Resident owner packet exceeds its bound")
        return packet, metric, len(rows)

    def for_group(self, references):
        """Join cached context fragments once for one physical runner's action."""
        rows = [self.contexts[index] for index in references]
        packet = "[" + ",".join(text for _, text, _ in rows) + "]"
        metric = array_measure(metric for _, _, metric in rows)
        if not rows or metric[0] > 4 * 1024 * 1024:
            raise ValueError("Shared regulation context table exceeds its bound")
        return packet, metric, len(rows)

    def seal(self):
        # Global input tables can be shared by owners, but must contain no unused
        # rows hidden outside the filtered guest packets.
        if len(self.used_contexts) != len(self.contexts) or len(self.used_selections) != len(self.selections):
            raise ValueError("Unreferenced resident action data")
