# SPDX-License-Identifier: MPL-2.0
"""Approach validation with per-test hooks, candidates and native records."""

import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallableTarget
from hook_manager.game_adapters.personnel import approach as native
from hook_manager.game_adapters.regulations.regulation_approach import RegulationApproachAdapter


@pytest.fixture
def approach_setup(monkeypatch):
    """Install approach validation for one test and stop its hooks during teardown."""
    module = ModuleType("owned_approaches")
    exec('''class Negotiations:
 def begin_approach(self, candidate_type, candidate, team_name, *, signer_kind="team", context=None, initiated_by="player"):
  row = {"person": candidate.name, "status": "approaching"}
  self.records.append(row)
  self.mail.append("Approach started")
  return True, "Approach started", row
 def _default_target_contract_start_week(self, candidate_type, candidate, team_name, *, signer_kind, context):
  return 53
''', module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    cls = module.Negotiations
    target = CallableTarget("personnel.approach", module.__name__, ("Negotiations",), "begin_approach",
                            str(inspect.signature(cls.begin_approach)))
    monkeypatch.setattr(native.PersonnelApproach, "target", target)
    monkeypatch.setattr(native, "method_group", lambda *a: {"_default_target_contract_start_week": (cls._default_target_contract_start_week, False)})
    monkeypatch.setattr(native, "driver_methods", lambda state: {})
    monkeypatch.setattr(native, "staff_methods", lambda state: {})
    state = SimpleNamespace(season_no=1, week_index=9, game_mode="formula",
        teams={"Audi": {"nationality": "german"}}, current_season_start_week=lambda: 1, next_season_start=lambda: 53)
    state.personnel_negotiation_manager = cls()
    manager = state.personnel_negotiation_manager
    manager.state, manager.records, manager.mail = state, [], []
    person = SimpleNamespace(name="Jenco", nationality="australian", age=21)
    seen = []
    # The class is created per fixture call, keeping refusal state private to each test.
    class Channel:
        _unavailable = set()
        validation_ready, allow = True, False

        def bindings(self, scope):
            return {"nationality": ((0, "rule"),)}

        def __call__(self, contexts, selections, calls, deadline):
            context = contexts[0]
            seen.append(context)
            if context["proposal"]["person"]["nationality"] == "german" or self.allow:
                return {"nationality": [{"decision": "allow"}]}
            return {"nationality": [{"decision": "deny", "message": "Nationality must match Audi (german)."}]}

    channel = Channel()
    guard = RegulationApproachAdapter(native.PersonnelApproach(state), channel, lambda rule, season: {"season": season}, lambda _: None)
    guard.install_prepared()
    yield manager, person, guard, channel, seen, cls
    guard.stop()

