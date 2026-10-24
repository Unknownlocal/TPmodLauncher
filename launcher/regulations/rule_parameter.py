# SPDX-License-Identifier: MPL-2.0
"""An optional value on the adopted option of an accepted regulation."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuleParameter:
    """Name a single key, not a path or executable expression.

    parameters holds the option's ordinary data; option_data holds this owner's
    extra data. Keeping the source explicit avoids silently merging those maps.
    Checking the rule reference is separate from resolving its selected value.
    """

    rule: str
    parameter: str
    source: str = "parameters"

    def declaration(self):
        return {"rule": self.rule, "parameter": self.parameter, "source": self.source}
