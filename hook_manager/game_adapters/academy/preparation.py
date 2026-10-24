# SPDX-License-Identifier: MPL-2.0
"""Give native academy AI private academy records, query inputs and random state."""

from copy import copy, deepcopy
import random
from types import SimpleNamespace


def copy_random(state, original):
    # Match the native RNG fallback, but consume a clone while the AI decides.
    # Allowance publishes its position; refusal must not consume career randomness.
    source = (getattr(state.academy_manager, "_rng", None) or getattr(state, "_rng", None)
              or original.__globals__.get("random", random))
    private = random.Random()
    private.setstate(source.getstate())
    if not callable(getattr(source, "setstate", None)):
        raise TypeError("Native academy random state cannot be published")
    return source, private


class AcademyPreparation:
    def __init__(self, state, methods, original):
        self.live = state
        self.state = copy(state)
        # Native AI writes academy data, not the rest of each team. Keep that
        # subtree private and borrow the remaining career data for read-only use.
        self.state.teams = {name: dict(team) for name, team in state.teams.items()}
        for team in self.state.teams.values():
            if "academy" in team:
                team["academy"] = deepcopy(team["academy"])
        self.manager = copy(state.academy_manager)
        self.manager.state = self.state
        self.state.academy_manager = self.manager
        self.touched, self.refused = set(), False
        self.methods = methods
        self.source_rng, self.rng = copy_random(state, original)
        self.manager._rng = self.rng
        # Eligibility asks active_contract, which normalizes its argument and
        # can fill an identity cache. Isolate that read without wrapping a driver
        # method already owned by the personnel integration.
        driver_manager = copy(state.driver_manager)
        driver_manager.state = self.state
        driver_manager._ai_signing_query_cache = None
        self.state.driver_manager = driver_manager
        active = methods["driver"]["active_contract"][0]

        def active_contract(person, week=None):
            private = SimpleNamespace(contract=deepcopy(person.contract))
            return active(driver_manager, private, week)

        driver_manager.active_contract = active_contract
        self.manager.ensure_team_state = self.ensure

    def ensure(self, team):
        name = next(name for name, record in self.state.teams.items() if record is team)
        result = self.methods["academy"]["ensure_team_state"][0](self.manager, team)
        # Failed normalization must not make a partially prepared team publishable.
        self.touched.add(name)
        return result

    def publish(self, name, *, random_state=True):
        if name in self.touched:
            prepared = self.state.teams[name]["academy"]
            live = self.live.teams[name].setdefault("academy", {})
            # Native normalization replaces root fields. Preserve canonical
            # contract/placement dictionaries and their driver-link aliases.
            for key in ("drivers", "contracts", "placements", "ai_last_sign_season",
                        "ai_last_placement_season", "ai_last_promotion_check"):
                if key in ("contracts", "placements") and type(live.get(key)) is dict:
                    continue
                if key in prepared:
                    live[key] = deepcopy(prepared[key])
        if random_state:
            self.source_rng.setstate(self.rng.getstate())

    def refresh(self, name):
        # Actual signing ran on the live manager. Later AI attempts need its new
        # roster, terms and balance rather than the pre-signing draft.
        live, prepared = self.live.teams[name], self.state.teams[name]
        prepared.clear()
        prepared.update(live)
        if "academy" in prepared:
            prepared["academy"] = deepcopy(prepared["academy"])

    def finish(self):
        if self.refused:
            # Earlier allowed actions already committed. Discard only preparation
            # still pending, including changes for teams that never attempted a sign.
            return
        for name in self.touched:
            self.publish(name, random_state=False)
        # If every attempted action was native/allowed, preserve the native RNG
        # progression even when affordability/prechecks prevented signing.
        self.source_rng.setstate(self.rng.getstate())
