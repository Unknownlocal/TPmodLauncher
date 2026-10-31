# SPDX-License-Identifier: MPL-2.0
"""Plain regulation input data shared by host and guest validation tests."""


def contract_context(**proposal):
    """Return fresh clock, contract-season and proposal dictionaries for each call."""
    return {"schema": 1, "scope": "personnel.contract", "operation": "commit", "initiator": "player",
            "clock": {"season": 2026, "absolute_week": 4},
            "effective": {"season": 2026, "start_week": 4, "end_week": None}, "proposal": proposal}


def voting_inputs(rule="native", *, padding=0):
    """Return fresh snapshot, option and team ballot data for a voting attempt."""
    snapshot = {"voting_model_version": 1, "season": 2026, "teams": {"A": {"cash": 10}, "B": {"cash": 20}},
                "results": [], "field": {"padding": "x" * padding}, "projected_races": 22}
    regulation = {"key": rule, "current": {"key": "open"}, "proposed": {"key": "closed"}}
    contexts = [{"schema": 1, "review": "review-2026", "proposal": "proposal-1", "decision_season": 2026,
                 "effective_season": 2027, "team": team, "seed": "fixed",
                 "native": {"choice": "current", "reason": "Native evaluation", "score": 7, "components": []}}
                for team in ("A", "B")]
    return snapshot, regulation, contexts

