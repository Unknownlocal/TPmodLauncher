# SPDX-License-Identifier: MPL-2.0
"""Replace declared team calculations while native code owns reviews and ballots."""

from copy import deepcopy
from functools import update_wrapper
import importlib
import threading
import time

from ...callables import CallableTarget
from ...manager import HookError, Manager
from .protocol import regulation_votes


class RegulationVotingAdapter(Manager):
    """Batch one proposal before the first native ballot write; keep saved votes."""

    @classmethod
    def create(cls, state, channel, report):
        if not getattr(channel, "voting", {}):
            return None
        return cls(state, channel, report)

    def __init__(self, state, channel, report):
        super().__init__(lambda record: None)
        self.state, self.channel, self.report = state, channel, report
        self._lock, self._local, self._active = threading.RLock(), threading.local(), 0
        try:
            # The fixed calculation module is safe to load during trusted setup;
            # author data cannot choose a module or native callable.
            importlib.import_module("managers.formula_regulation_voting_ai")
            bindings = []
            methods = (("_open_review", "(self, season, week)"),
                       ("_resolve_uncontrolled_reviews", "(self)"), ("_electorate", "(self)"))
            for method, signature in methods:
                target = CallableTarget("regulations.voting." + method, "managers.formula_regulation_governance",
                                        ("FormulaRegulationGovernanceManager",), method, signature)
                self.approve(target)
                bindings.append({"target": target.name, "callback": "internal", "phase": "after"})
            target = CallableTarget("regulations.voting.calculate", "managers.formula_regulation_voting_ai", (),
                                    "evaluate_vote", "(snapshot: 'dict', category_key: 'str', current_option: 'dict', "
                                    "proposed_option: 'dict', voter_team: 'str', seed: 'str') -> 'dict'")
            self.approve(target)
            bindings.append({"target": target.name, "callback": "internal", "phase": "after"})
            self.install("regulations.voting", bindings)
            group = self.groups["regulations.voting"]
            if group["state"] != "active" or state.formula_regulation_governance.state is not state:
                raise HookError("Native voting installation is incomplete")
            if not self.channel.voting.keys() <= state.formula_regulation_governance.definitions.keys():
                raise HookError("A declared voting category is absent from this career")
        except BaseException:
            self.stop()
            raise

    def _wrapper(self, name, binding, target, original, signature):
        """Replace calculations only inside this career's native review calls."""
        group, method = self.groups[name], target.attribute

        def wrapped(*args, **kwargs):
            if group["state"] != "active":
                return original(*args, **kwargs)
            values = signature.bind(*args, **kwargs).arguments
            scope = getattr(self._local, "scope", None)
            if method == "evaluate_vote":
                if scope is None or values["category_key"] not in self.channel.voting:
                    return original(*args, **kwargs)
                return self._calculate(scope, original, values)
            manager = values["self"]
            if method == "_electorate":
                result = original(*args, **kwargs)
                if scope is not None and scope["manager"] is manager and scope["opening"]:
                    # Capture the electorate native opening actually chose,
                    # rather than querying a second potentially changed roster.
                    scope["electorate"] = deepcopy(result)
                return result
            if manager.state is not self.state:
                return original(*args, **kwargs)
            with self._lock:
                if self._active:
                    self.report("REGULATIONS COMPATIBILITY FAILED voting: review calculation already active; review skipped")
                    return None
                self._active += 1
            scope = {"manager": manager, "opening": method == "_open_review", "cache": {},
                     "deadline": getattr(self._local, "deadline", None)}
            cooldowns = None
            try:
                cooldowns = dict(manager.category_last_review)
                self._local.scope = scope
                return original(*args, **kwargs)
            except HookError as error:
                if scope["opening"] and cooldowns is not None:
                    manager.category_last_review = cooldowns
                elif hasattr(manager, "_tpm_voting_pending"):
                    manager._tpm_voting_pending = True
                self.report("REGULATIONS COMPATIBILITY FAILED voting: " + str(error)[:512]
                            + "; custom review calculation skipped")
                return None
            finally:
                self._local.scope = None
                with self._lock:
                    self._active -= 1

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped

    def resume(self):
        """Fill missing saved ballots after all required checks are enabled."""
        manager = self.state.formula_regulation_governance
        if getattr(manager, "_tpm_voting_pending", False):
            manager._tpm_voting_pending = False
            # All replayed proposals share one activation allowance. Per-team
            # and per-proposal limits can shorten it, never renew it.
            self._local.deadline = time.monotonic() + 1 + 5 * len(getattr(self.channel, "_owners", {}))
            try:
                manager._resolve_uncontrolled_reviews()
            finally:
                self._local.deadline = None
            if manager._tpm_voting_pending:
                raise HookError("Deferred saved voting could not complete")
            del manager._tpm_voting_pending

    def _calculate(self, scope, original, values):
        """Finish every missing controller's calculation before native ballot writes."""
        category, seed = values["category_key"], values["seed"]
        current, proposed = values["current_option"], values["proposed_option"]
        identity = category, current["key"], proposed["key"], seed
        if identity not in scope["cache"]:
            try:
                manager = scope["manager"]
                proposal = review = None
                if scope["opening"]:
                    electorate = scope["electorate"]
                    season = values["snapshot"]["season"]
                else:
                    # Saved proposal identity and electorate are authoritative,
                    # including controllers no longer present in the live field.
                    for candidate in manager.reviews.values():
                        for row in candidate["proposals"]:
                            if (row["category_key"] == category and manager.seed + row["id"] == seed):
                                review, proposal = candidate, row
                    if proposal is None:
                        raise HookError("Native vote does not belong to a saved proposal")
                    if (values["snapshot"] is not review["snapshot"] or current != proposal["current_option"]
                            or proposed != proposal["proposed_option"]):
                        raise HookError("Native calculation does not use the saved review inputs")
                    electorate, season = review["electorate"], review["season"]
                frozen_electorate = deepcopy(electorate)
                groups = dict.fromkeys(row["controller_team"] for row in electorate)
                recorded = deepcopy(proposal["votes"]) if proposal is not None else {}
                for voter in electorate:
                    vote = recorded.get(voter["id"])
                    if vote is not None:
                        answer = {key: vote[key] for key in ("choice", "reason")}
                        team = voter["controller_team"]
                        if groups[team] is not None and groups[team] != answer:
                            raise HookError("Saved controller ballots disagree")
                        groups[team] = answer
                contexts = []
                # Identifiers are opaque; the native seed avoids depending on
                # how the game formats or separates its review/proposal IDs.
                for team, answer in groups.items():
                    if answer is None:
                        native = original(values["snapshot"], category, current, proposed, team, seed)
                        contexts.append({"schema": 1, "review": str(season), "proposal": seed,
                                         "decision_season": season, "effective_season": season + 1,
                                         "team": team, "native": native, "seed": seed})
                if contexts:
                    if not self.channel.validation_ready or self.channel.closed:
                        raise HookError("Voting career is unavailable")
                    # Reviews also retain calendar bookkeeping. Only the
                    # supported voting inputs cross into author code.
                    snapshot = {key: values["snapshot"][key] for key in
                                ("voting_model_version", "season", "teams", "results", "field", "projected_races")}
                    results = self.channel.vote(snapshot, {"key": category, "current": current, "proposed": proposed},
                                                contexts, deadline=scope["deadline"])
                    regulation_votes(results, [row["team"] for row in contexts])
                    if (manager is not self.state.formula_regulation_governance
                            or not self.channel.validation_ready or self.channel.closed
                            or electorate != frozen_electorate
                            or proposal is not None and (proposal["votes"] != recorded or proposal["status"] != "open"
                                                        or manager.reviews.get(review["id"]) is not review)):
                        raise HookError("Native voting inputs changed during calculation")
                    groups.update(results)
                scope["cache"][identity] = groups
            except Exception as error:
                detail = " ".join(str(error).split())[:256]
                raise HookError("Custom voting unavailable for " + category + ": " + detail) from error
        try:
            return dict(scope["cache"][identity][values["voter_team"]])
        except (KeyError, TypeError) as error:
            raise HookError("Native calculation addresses an unknown controller") from error
