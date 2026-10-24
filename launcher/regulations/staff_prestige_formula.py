# SPDX-License-Identifier: MPL-2.0
"""An owned prestige formula and the regulation records it declares as inputs."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StaffPrestigeFormula:
    """Keep a handler name without importing its Python module.

    rules names native or owner-local categories, not filenames or game objects.
    Copied staff inputs are separate from this list; the declaration does not
    limit them to the skill inputs used by the game's original formula.
    """

    handler: str
    rules: tuple[str, ...]

    def declaration(self):
        return {"handler": self.handler, "data": list(self.rules)}
