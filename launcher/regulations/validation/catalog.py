# SPDX-License-Identifier: MPL-2.0
"""Shared native bases, owner-private records and reverse rule subscriptions."""

from collections import ChainMap

from mod_runtime._regulations.policy import encode
from mod_runtime._regulations.values import bounded, clone, integer, measure, text
from ..definitions import RegulationDefinitions
from .record import CatalogRecord
from .view import OwnerView

_PRESENTATION = frozenset(("id", "key", "name", "summary", "description", "parameters"))


class CombinedCatalog:
    def __init__(self, definitions):
        if type(definitions) is not RegulationDefinitions:
            raise ValueError("Expected admitted regulation definitions")
        native, owners, revisions = definitions._catalog_inputs()
        self.native = native
        self.owners = {owner: (revisions[owner], tuple(sorted(model.validators, key=lambda row: row["id"])), model)
                       for owner, model in owners.items() if model.validators}
        self.custom = {row["key"]: (owner, {option["key"]: option for option in row["options"]})
                       for owner, (_, _, model) in self.owners.items() for row in model.categories}
        self.subscribers = {}
        for owner, (_, validators, _) in self.owners.items():
            for rule in {row["rule"] for row in validators}:
                self.subscribers.setdefault(rule, set()).add(owner)
        self.views, self._choices, self._records = {}, {}, {}
        self._next = 0
        self._staged = None

    def affected(self, native, custom):
        if type(native) is not dict or type(custom) is not dict or set(native) & set(custom):
            raise ValueError("Expected disjoint native and custom rule updates")
        keys = native.keys() | custom.keys()
        if not keys <= self.subscribers.keys():
            raise ValueError("Publication targets an unsubscribed rule")
        if any(key not in self.native for key in native) or any(key not in self.custom for key in custom):
            raise ValueError("Native publication cannot replace custom definitions")
        if not self.views and keys != self.subscribers.keys():
            raise ValueError("Initial publication must cover every required rule")
        return set().union(*(self.subscribers[key] for key in keys))

    def _record(self, cache, namespace, rule, key, kind, value, *, borrow=False):
        if type(value) is not dict:
            raise ValueError("Expected a plain regulation payload object")
        # Admitted private payloads cannot change during this pack session. Their
        # owner/rule/option identity avoids even an encoding on later publications.
        identity = namespace, rule, key, kind
        if borrow and identity in cache:
            return cache[identity]
        encoded = encode(value)
        if not borrow:
            identity = *identity, encoded
        if identity not in cache:
            metric = bounded(measure(value, encoded=encoded))
            self._next = integer(self._next + 1, 64, positive=True)
            cache[identity] = CatalogRecord(self._next, kind, value if borrow else clone(value), encoded, metric)
        return cache[identity]

    def _native_choices(self, rule, options, cache):
        if type(options) is not list or not options:
            raise ValueError("A reachable rule needs its complete nonempty choice list")
        result, seen = [], set()
        for option in options:
            if type(option) is not dict:
                raise ValueError("Expected an effective native option object")
            key = text(option["key"], 128)
            parameters = option.get("parameters", {})
            if type(parameters) is not dict:
                raise ValueError("Expected native parameters")
            flat = {name: value for name, value in option.items() if name not in _PRESENTATION}
            if flat.keys() & parameters.keys():
                raise ValueError("Native flat and explicit parameters collide")
            # Effective objects may be saved replacements or synthesized options.
            # Preserve unfamiliar operational fields; presentation is excluded.
            flat.update(parameters)
            record = self._record(cache, None, rule, key, "parameters", flat)
            if (key, record.identity) in seen:
                raise ValueError("Repeated reachable native choice")
            seen.add((key, record.identity))
            result.append((key, record))
        return tuple(result)

    def stage(self, native, custom):
        if self._staged is not None:
            raise ValueError("A catalog publication is already staged")
        affected = self.affected(native, custom)
        # Stage only new records; unchanged canonical objects and their lookup
        # index are borrowed until commit flattens the retained reachable set.
        cache, choices = ChainMap({}, self._records), dict(self._choices)
        for rule, options in native.items():
            if options is None:
                # Explicit generation-only update: retain every adopted choice.
                # Initial setup cannot borrow membership that was never installed.
                if rule not in choices:
                    raise ValueError("Unchanged membership needs an active native rule")
                continue
            choices[rule] = self._native_choices(rule, options, cache)
        for rule, keys in custom.items():
            if keys is None:
                # Custom rules use the same unchanged-membership signal; a real
                # change below still resolves keys against admitted data only.
                if rule not in choices:
                    raise ValueError("Unchanged membership needs an active custom rule")
                continue
            owner, admitted = self.custom[rule]
            if (type(keys) is not list or not keys or any(type(key) is not str or key not in admitted for key in keys)
                    or len(set(keys)) != len(keys)):
                raise ValueError("Custom choices must resolve against admitted owner options")
            choices[rule] = tuple((key, self._record(cache, owner, rule, key, "parameters",
                                                  admitted[key].get("parameters", {}), borrow=True)) for key in keys)
        views = dict(self.views)
        for owner in sorted(affected):
            revision, validators, model = self.owners[owner]
            owned = {}
            for rule in sorted({row["rule"] for row in validators}):
                owned[rule] = []
                for key, base in choices[rule]:
                    # Only this owner's admitted overlay can accompany a base.
                    # Synthesized native keys have an empty overlay when absent.
                    value = model.option_data.get(rule, {}).get(key, {})
                    overlay = self._record(cache, owner, rule, key, "option_data", value, borrow=True)
                    owned[rule].append((key, base, overlay))
            view = OwnerView(owner, revision, validators, owned)
            previous = self.views.get(owner)
            views[owner] = previous if previous is not None and previous.signature == view.signature else view
        self._staged = choices, views, cache
        return {owner: views[owner] for owner in sorted(affected)}

    def commit(self):
        if self._staged is None:
            raise ValueError("No staged catalog publication")
        self._choices, self.views, cache = self._staged
        reachable = {record.identity for view in self.views.values() for record in view.records}
        self._records = {key: record for key, record in cache.items() if record.identity in reachable}
        self._staged = None

    def abort(self):
        self._staged = None

    def reset(self):
        self.views, self._choices, self._records = {}, {}, {}
        self._staged = None
        # IDs never repeat, even across a career reset or an aborted publication.
