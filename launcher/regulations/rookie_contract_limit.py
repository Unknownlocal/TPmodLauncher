# SPDX-License-Identifier: MPL-2.0
"""The source of a rookie maximum; the game decides which drivers are rookies."""

from dataclasses import dataclass

from .rule_parameter import RuleParameter


def rookie_maximum(value):
    """Check a configured maximum without converting booleans or decimals."""
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError("Rookie maximum must be a positive integer or null")
    return value


def apply_rookie_maximum(native_maximum, limit):
    """Apply a prepared scalar only where the original helper found a rookie cap."""
    rookie_maximum(native_maximum)
    # Native None excludes this driver/role; limit None means the optional
    # parameter was absent. A limit model holding None explicitly removes a cap.
    return (native_maximum if native_maximum is None or limit is None
            else rookie_maximum(limit.value))


@dataclass(frozen=True, slots=True)
class RookieContractLimit:
    """Retain a literal maximum or a reference resolved on policy publication.

    A model containing None means an explicit unlimited cap. No model means no
    replacement, so those two declarations cannot accidentally become equivalent.
    """

    value: int | None | RuleParameter

    @property
    def rules(self):
        return (self.value.rule,) if isinstance(self.value, RuleParameter) else ()

    def declaration(self):
        return self.value.declaration() if isinstance(self.value, RuleParameter) else {"value": self.value}

    def resolve(self, choices, option_data):
        """Read one scalar per reachable choice from borrowed catalogue records."""
        if not isinstance(self.value, RuleParameter):
            return (self,)
        source, values = self.value, []
        for key, base in choices[source.rule]:
            # Keep native parameters and the owner's overlay separate. Missing
            # keys mean native default; a model holding None means no rookie cap.
            data = (base.value if source.source == "parameters"
                    else option_data.get(source.rule, {}).get(key, {}))
            values.append(RookieContractLimit(rookie_maximum(data[source.parameter]))
                          if source.parameter in data else None)
        return tuple(values)
