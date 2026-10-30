# SPDX-License-Identifier: MPL-2.0
"""Review boundaries preserve ballots and publish only complete calculations."""

from copy import deepcopy
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations.regulation_voting import RegulationVotingAdapter
from hook_manager.game_adapters.regulations.regulation_change import RegulationChangeAdapter


@pytest.fixture
def voting(monkeypatch, tmp_path):
    ai = ModuleType("managers.formula_regulation_voting_ai")
    def evaluate_vote(snapshot: 'dict', category_key: 'str', current_option: 'dict', proposed_option: 'dict', voter_team: 'str', seed: 'str') -> 'dict':
        return {"choice": "current", "reason": "Native", "score": 7, "components": []}
    ai.evaluate_vote = evaluate_vote
    native = ModuleType("managers.formula_regulation_governance")
    class Reviews:
        def __init__(self, state, saved_bootstrap=None):
            self.state = state
            state.formula_regulation_governance = self
            self.definitions, self.seed = {"custom": {}, "native": {}}, "seed"
            self.category_last_review, self.reviews = {}, {}
            self.electorate = [{"id": team, "controller_team": team} for team in ("player", "A", "B")]
        def load_state(self, data):
            self.reviews = deepcopy(data["reviews"])
            self._resolve_uncontrolled_reviews()
        def save_state(self):
            return {"reviews": deepcopy(self.reviews)}
        def _electorate(self):
            return self.electorate
        def _open_review(self, season, week):
            electorate = self._electorate()
            snapshot = {"season": season, "voting_model_version": 1, "teams": {}, "results": [], "field": {},
                        "projected_races": 0, "calendar_tracks": ["private"], "calendar_schedule": []}
            proposals = []
            for key in ("native", "custom"):
                proposal = {"id": key, "category_key": key, "current_option": {"key": "a"},
                            "proposed_option": {"key": "b"}, "votes": {}, "status": "open"}
                self.category_last_review[key] = season
                for row in electorate:
                    answer = ai.evaluate_vote(snapshot, key, proposal["current_option"], proposal["proposed_option"],
                                              row["controller_team"], self.seed + proposal["id"])
                    if row["id"] == "player":
                        proposal["player_analysis"] = answer
                    else:
                        proposal["votes"][row["id"]] = answer
                proposals.append(proposal)
            self.reviews["review"] = {"id": "review", "snapshot": snapshot, "season": season,
                                      "electorate": deepcopy(electorate), "proposals": proposals}
        def _resolve_uncontrolled_reviews(self):
            for review in self.reviews.values():
                for proposal in review["proposals"]:
                    for voter in review["electorate"]:
                        if voter["id"] not in proposal["votes"]:
                            proposal["votes"][voter["id"]] = ai.evaluate_vote(review["snapshot"], proposal["category_key"],
                                proposal["current_option"], proposal["proposed_option"], voter["controller_team"], self.seed + proposal["id"])
    native.FormulaRegulationGovernanceManager = Reviews
    monkeypatch.setitem(sys.modules, native.__name__, native)
    monkeypatch.setitem(sys.modules, ai.__name__, ai)
    manager, notices, batches = Reviews(SimpleNamespace(data_dir=str(tmp_path))), [], []
    def vote(snapshot, regulation, contexts, **kwargs):
        assert set(snapshot) == {"season", "voting_model_version", "teams", "results", "field", "projected_races"}
        batches.append(deepcopy(contexts))
        return {row["team"]: {"choice": "proposed", "reason": "Custom"} for row in contexts}
    channel = SimpleNamespace(voting={"custom": "owner"}, validation_ready=True, closed=False, vote=vote)
    adapter = RegulationVotingAdapter.create(manager.state, channel, notices.append)
    channel.adapter = adapter
    try:
        yield manager, channel, notices, batches, ai
    finally:
        adapter.stop()


def test_saved_electorate_recorded_ballots_and_player_analysis(voting):
    manager, channel, notices, batches, ai = voting
    manager._open_review(3, 30)
    review = manager.reviews["review"]
    native, custom = review["proposals"]
    assert native["player_analysis"]["score"] == 7
    assert "player" not in custom["votes"] and custom["player_analysis"] == {"choice": "proposed", "reason": "Custom"}
    assert len(batches) == 1 and len(batches[0]) == 3
    assert all(row["native"]["score"] == 7 for row in batches[0])
    # A controller's recorded preference also supplies its other missing ballots.
    review["electorate"].append({"id": "affiliate", "controller_team": "A"})
    player = custom["votes"]["player"] = {"choice": "current", "reason": "Player ballot"}
    del custom["votes"]["B"]
    manager.electorate = [{"id": "new", "controller_team": "new"}]
    manager._resolve_uncontrolled_reviews()
    assert len(batches) == 2 and [row["team"] for row in batches[1]] == ["B"]
    assert custom["votes"]["player"] is player and custom["votes"]["affiliate"] == custom["votes"]["A"]
    assert ai.evaluate_vote({}, "custom", {}, {}, "external", "seed")["reason"] == "Native"
    assert not notices


def test_failed_batches_do_not_publish_opening_or_missing_ballots(voting):
    manager, channel, notices, batches, _ = voting
    valid = channel.vote
    def invalid(*args, **kwargs):
        return {"A": {"choice": "proposed", "reason": "Incomplete"}}
    channel.vote = invalid
    manager._open_review(3, 30)
    assert not manager.reviews and not manager.category_last_review and notices
    assert "Incomplete voting results" in notices[0]
    channel.vote = valid
    manager._open_review(3, 30)
    custom = manager.reviews["review"]["proposals"][1]
    custom["votes"].clear()
    before = deepcopy(custom)
    channel.vote = invalid
    manager._resolve_uncontrolled_reviews()
    assert custom == before and len(notices) == 2


def test_player_ballot_cast_during_calculation_is_kept(voting):
    manager, channel, notices, _, _ = voting
    manager._open_review(3, 30)
    custom = manager.reviews["review"]["proposals"][1]
    custom["votes"].clear()
    valid = channel.vote
    def player_votes(*args, **kwargs):
        custom["votes"]["player"] = {"choice": "current", "reason": "Manual ballot"}
        return valid(*args, **kwargs)
    channel.vote = player_votes
    manager._resolve_uncontrolled_reviews()
    assert custom["votes"] == {"player": {"choice": "current", "reason": "Manual ballot"}}
    assert len(notices) == 1


def test_save_loading_defers_missing_ballots_until_activation(voting, tmp_path):
    manager, channel, notices, batches, _ = voting
    manager._open_review(3, 30)
    saved = manager.save_state()
    for proposal in saved["reviews"]["review"]["proposals"]:
        proposal["votes"].clear()
    channel.adapter.stop()
    changes = RegulationChangeAdapter(notices.append)
    try:
        changes.defer_voting(str(tmp_path))
        manager.load_state(saved)
        assert manager._tpm_voting_pending and "_resolve_uncontrolled_reviews" not in vars(manager)
        assert all(not p["votes"] for p in manager.reviews["review"]["proposals"])
        replacement = RegulationVotingAdapter(manager.state, channel, notices.append)
        try:
            replacement.resume()
            assert not hasattr(manager, "_tpm_voting_pending")
            assert len(batches) == 2 and len(manager.reviews["review"]["proposals"][1]["votes"]) == 3
            assert not notices
        finally:
            replacement.stop()
    finally:
        changes.stop()
