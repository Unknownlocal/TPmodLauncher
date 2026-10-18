# SPDX-License-Identifier: MPL-2.0
"""The regulation-specific model for one admitted owner's declarations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OwnerDefinition:
    """Own converted declarations without interpreting opaque condition data.

    The converter transfers private containers to this model after validation.
    Frozen fields prevent reassignment, not nested mutation; retained containers
    stay internal. The aggregate copies declarations before exposing outward views.
    Native/category option fields remain dictionaries to preserve their format.
    """

    categories: list[dict]
    option_data: dict[str, dict]
    validators: list[dict[str, str]]
    scripted_changes: list[dict]

    def declarations(self):
        """Build a borrowed schema-1 view for internal copying/serialization.

        Only the outer mapping is new; nested containers still belong to this
        model. The aggregate copies this view before returning it to consumers.
        Keeping the internal view borrowed avoids a deep copy just for hashing.
        """
        return {"schema": 1, "categories": self.categories,
                "option_data": self.option_data, "validators": self.validators,
                "scripted_changes": self.scripted_changes}
