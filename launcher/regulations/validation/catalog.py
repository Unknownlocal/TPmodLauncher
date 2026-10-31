# SPDX-License-Identifier: MPL-2.0
"""Keep one launcher-side catalog for all participating regulation mods.

Share native rule values among subscribing mods while keeping each mod's custom
rules and extra option data separate. Each career installs all subscribed rules
once. The shared runner retains the installed copies.
"""

from copy import deepcopy

from hook_manager.game_adapters.regulations.protocol import regulation_native_option
from mod_runtime._regulations.policy import encode
from mod_runtime._regulations.values import bounded, integer, measure
from ..definitions import RegulationDefinitions
from ..regulation_handler_bindings import ordered_bindings
from .record import CatalogRecord
from .view import OwnerView


class CombinedCatalog:
    def __init__(self, definitions):
        if type(definitions) is not RegulationDefinitions:
            raise ValueError("Expected admitted regulation definitions")
        native, owners, revisions = definitions._catalog_inputs()
        self.native = native
        # Admission has already rejected competing owners for each fixed bridge.
        # Keep the small declarations, not another owner/revision lifecycle.
        self._native_rules = {name: value for model in owners.values() for name, value in model.native_rules.items()}
        self.owners = {owner: (revisions[owner], ordered_bindings(model.validators), model)
                       for owner, model in owners.items() if model.validators}
        self.custom = {row["key"]: (owner, {option["key"]: option for option in row["options"]})
                       for owner, model in owners.items() for row in model.categories}
        self.rules = {row["rule"] for _, validators, _ in self.owners.values() for _, row in validators.values()}
        self.views = {}
        self._next = 0
        self._staged = None
        self._published, self._failed = False, False

    @property
    def native_values_ready(self):
        """Include native-only declarations even without a guest handler."""
        return not self._failed and self._published

    def native_rules(self):
        """Copy admitted switch declarations after a complete policy publication."""
        if not self.native_values_ready:
            raise RuntimeError("Native rule policy is unavailable")
        return deepcopy(self._native_rules)

    def fail_native_values(self):
        # Native-only rules have no guest state to fence. Retire their values
        # too, including when a deadline expires after catalogue commit.
        self._published, self._failed = False, True

    def check_publication(self, native, custom):
        """Require one complete membership after admission or career reset."""
        if self._failed:
            raise RuntimeError("Native rule policy is unavailable")
        if self._published or self._staged is not None:
            raise ValueError("Regulation policy is already installed; reset for a new career")
        if type(native) is not dict or type(custom) is not dict or native.keys() & custom.keys():
            raise ValueError("Expected disjoint native and custom rule maps")
        if native.keys() | custom.keys() != self.rules:
            raise ValueError("Publication must cover every required rule")
        if any(key not in self.native for key in native) or any(key not in self.custom for key in custom):
            raise ValueError("Native publication cannot replace custom definitions")

    def _record(self, cache, namespace, rule, key, kind, value, *, borrow=False):
        if type(value) is not dict:
            raise ValueError("Expected a plain regulation payload object")
        # Private values share by owner/rule/option; native values also include
        # their encoded payload because saved choices can reuse an option key.
        identity = namespace, rule, key, kind
        if borrow and identity in cache:
            return cache[identity]
        encoded = encode(value)
        if not borrow:
            identity = *identity, encoded
        if identity not in cache:
            metric = bounded(measure(value, encoded=encoded))
            self._next = integer(self._next + 1, 64, positive=True)
            cache[identity] = CatalogRecord(self._next, kind, encoded, metric)
        return cache[identity]

    def _native_choices(self, rule, options, cache):
        if type(options) is not list or not options:
            raise ValueError("A reachable rule needs its complete nonempty choice list")
        result, seen = [], set()
        for option in options:
            option = regulation_native_option(option)
            key = option["key"]
            record = self._record(cache, None, rule, key, "parameters", option["parameters"])
            if (key, record.identity) in seen:
                raise ValueError("Repeated reachable native choice")
            seen.add((key, record.identity))
            result.append((key, record))
        return tuple(result)

    def stage(self, native, custom):
        self.check_publication(native, custom)
        # Build one candidate without changing the installed views. Only commit
        # exposes it after every owner's complete guest install is acknowledged.
        cache, choices = {}, {}
        for rule, options in native.items():
            choices[rule] = self._native_choices(rule, options, cache)
        for rule, keys in custom.items():
            owner, admitted = self.custom[rule]
            if (type(keys) is not list or not keys or any(type(key) is not str or key not in admitted for key in keys)
                    or len(set(keys)) != len(keys)):
                raise ValueError("Custom choices must resolve against admitted owner options")
            choices[rule] = tuple((key, self._record(cache, owner, rule, key, "parameters",
                                                  admitted[key].get("parameters", {}), borrow=True)) for key in keys)
        views = {}
        for owner in self.owners:
            revision, validators, model = self.owners[owner]
            owned = {}
            for rule in sorted({row["rule"] for _, row in validators.values()}):
                owned[rule] = []
                for key, base in choices[rule]:
                    # Only this owner's admitted overlay can accompany a base.
                    # Synthesized native keys have an empty overlay when absent.
                    value = model.option_data.get(rule, {}).get(key, {})
                    overlay = self._record(cache, owner, rule, key, "option_data", value, borrow=True)
                    owned[rule].append((key, base, overlay))
            views[owner] = OwnerView(owner, revision, validators, owned)
        self._staged = views
        return views

    def commit(self):
        if self._staged is None:
            raise ValueError("No staged catalog publication")
        self.views, self._staged = self._staged, None
        self._published = True

    def abort(self):
        self._staged = None

    def reset(self):
        if self._failed:
            raise RuntimeError("Reset cannot revive an unavailable native rule policy")
        self._published = False
        self.views = {}
        self._staged = None
        # IDs never repeat, even across a career reset or an aborted publication.
