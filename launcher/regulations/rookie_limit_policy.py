# SPDX-License-Identifier: MPL-2.0
"""Resolve a rookie cap once per publication from the shared catalogue records."""

from mod_runtime._regulations.values import integer

from .rookie_contract_limit import RookieContractLimit, rookie_maximum
from .rookie_limit_binding import RookieLimitBinding
from .rule_parameter import RuleParameter


class RookieLimitPolicy:
    """Stage small cap tables alongside the catalogue, publishing them on commit.

    Definitions and option records are borrowed from their existing owners.
    Resolution reads one scalar per choice: it does not copy, encode or decode
    their payloads, and it never enters Python mod code.
    """

    def __init__(self, definitions):
        _, owners, revisions = definitions._catalog_inputs()
        self.owner, self.rule = None, None
        self._binding = self._staged = None
        self._failed = False
        for owner, model in owners.items():
            declaration = model.native_rules.get("rookie_contract_max_years")
            if declaration is not None:
                # Admission has already rejected competing declarations.
                self.owner, self._revision = owner, revisions[owner]
                self._source, self._overlays = declaration.value, model.option_data
                if isinstance(self._source, RuleParameter):
                    self.rule = self._source.rule
                break

    @property
    def binding(self):
        """Return no binding for an absent declaration; uncommitted data is an error."""
        if self.owner is not None and self._failed:
            raise RuntimeError("Rookie limit policy is unavailable")
        if self.owner is not None and self._binding is None:
            raise RuntimeError("Rookie limit policy is not published")
        return self._binding

    @property
    def ready(self):
        # Literal-only rules have no guest handler whose state could otherwise
        # keep the dispatcher unready before publication or after failure.
        return self.owner is None or (not self._failed and self._binding is not None and self._staged is None)

    def stage(self, choices, *, epoch, generation):
        if self.owner is None:
            return
        if self._failed:
            raise RuntimeError("Rookie limit policy is unavailable")
        # Identities come from the existing publisher. This policy does not
        # maintain a second generation counter or guess the career's season.
        integer(epoch, 64, positive=True)
        integer(generation, 64, positive=True)
        if self._staged is not None:
            raise RuntimeError("Rookie limit publication is already staged")
        if self._binding is not None and (epoch != self._binding.epoch or generation <= self._binding.generation):
            raise ValueError("Rookie limit publication needs a newer policy generation")
        if self.rule is None:
            values = (RookieContractLimit(self._source),)
        else:
            values = []
            for key, base in choices[self.rule]:
                # Native saved options use their actual operational parameters;
                # option_data is always this owner's separate admitted overlay.
                data = (base.value if self._source.source == "parameters"
                        else self._overlays.get(self.rule, {}).get(key, {}))
                value = (RookieContractLimit(rookie_maximum(data[self._source.parameter]))
                         if self._source.parameter in data else None)
                values.append(value)
            values = tuple(values)
        self._staged = RookieLimitBinding(self.owner, self._revision, epoch, generation, self.rule, values)

    def commit(self):
        if self.owner is not None:
            if self._staged is None:
                raise RuntimeError("No rookie limit publication is staged")
            self._binding, self._staged = self._staged, None

    def abort(self):
        # Cancelling a candidate leaves the current publication intact. Actual
        # dispatch failure uses fail() to revoke the committed binding as well.
        self._staged = None

    def reset(self):
        if self.owner is not None and self._failed:
            raise RuntimeError("Reset cannot revive an unavailable rookie limit policy")
        self._binding = self._staged = None

    def fail(self):
        # A late publication can fail after host commit. Retire that committed
        # cap too; no guest owner exists to fence a literal-only replacement.
        self._binding = self._staged = None
        self._failed = True
