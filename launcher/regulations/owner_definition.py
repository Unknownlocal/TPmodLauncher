# SPDX-License-Identifier: MPL-2.0
"""The regulation-specific model for one admitted owner's declarations."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class OwnerDefinition:
    """Own converted declarations without interpreting opaque condition data.

    The converter transfers private containers to this model after validation.
    Frozen fields prevent reassignment, not nested mutation; retained containers
    stay internal. The aggregate copies only the fields needed by native loading and binding.
    Native/category option fields remain dictionaries to preserve their format.
    """

    categories: list[dict]
    option_data: dict[str, dict]
    validators: list[dict[str, str]]
    scripted_changes: list[dict]
    native_rules: dict[str, bool | dict[str, str]] = field(default_factory=dict)

    def rule_keys(self):
        """List categories needed for payload checks and revision tracking."""
        return self.option_data.keys() | {row["rule"] for row in self.validators}

    def declarations(self):
        """Build a borrowed declaration view for revision hashing.

        Revision encoding reads these private containers without copying them.
        """
        result = {"schema": 1, "categories": self.categories,
                  "option_data": self.option_data, "validators": self.validators,
                  "scripted_changes": self.scripted_changes}
        # Omit absent switches from revision input. Any category referenced by a
        # switch is included in this model's declarations.
        if self.native_rules:
            result["native_rules"] = self.native_rules
        return result
