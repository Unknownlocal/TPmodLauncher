# SPDX-License-Identifier: MPL-2.0
"""Keep only checked rookie maxima and the policy identity that supplied them."""

from dataclasses import dataclass

from mod_runtime._regulations.values import integer

from .rookie_contract_limit import RookieContractLimit, rookie_maximum


@dataclass(frozen=True, slots=True)
class RookieLimitBinding:
    """A small native-helper input, not permission to sign a contract.

    A missing value keeps the original cap; RookieContractLimit(None) explicitly
    removes it. Choice indexes preserve publication order, including different
    saved options that have the same key. No game objects or rule payloads remain.
    """

    owner: str
    revision: str
    epoch: int
    generation: int
    rule: str | None
    values: tuple[RookieContractLimit | None, ...]

    def apply(self, native_maximum, *, epoch, generation, choice=None):
        """Adjust an already-computed native result for an explicitly chosen option.

        The caller supplies live policy identities and the current/upcoming
        choice. This helper neither guesses a season nor decides rookie status.
        """
        integer(epoch, 64, positive=True)
        integer(generation, 64, positive=True)
        if (epoch, generation) != (self.epoch, self.generation):
            raise ValueError("Stale rookie limit binding")
        rookie_maximum(native_maximum)
        if self.rule is None:
            if choice is not None:
                raise ValueError("Literal rookie maximum needs no rule choice")
            value = self.values[0]
        else:
            if type(choice) is not int or not 0 <= choice < len(self.values):
                raise ValueError("Rookie maximum needs a published rule choice")
            value = self.values[choice]
        # None from the original helper excludes this person/role from the
        # native rookie rule. Even a configured cap cannot widen applicability.
        if native_maximum is None or value is None:
            return native_maximum
        return value.value
