# SPDX-License-Identifier: MPL-2.0
"""The source of a rookie maximum; the game decides which drivers are rookies."""

from dataclasses import dataclass

from .rule_parameter import RuleParameter


def rookie_maximum(value):
    """Check a configured maximum without converting booleans or decimals."""
    if value is not None and (type(value) is not int or value < 1):
        raise ValueError("Rookie maximum must be a positive integer or null")
    return value


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
