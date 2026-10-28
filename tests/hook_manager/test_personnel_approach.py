# SPDX-License-Identifier: MPL-2.0
"""An approach refusal must reach native callers before negotiation writes."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
import inspect
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallableTarget
from hook_manager.game_adapters.personnel import approach as native
from hook_manager.game_adapters.regulations.approach_adapter import ApproachAdapter


@pytest.fixture
def setup(monkeypatch):
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
    channel = SimpleNamespace(_unavailable=set(), validation_ready=True, allow=False)
    channel.bindings = lambda scope: {"nationality": ((0, "rule"),)}

    class Channel:
        def bindings(self, scope):
            return channel.bindings(scope)
        _unavailable = channel._unavailable
        @property
        def validation_ready(self):
            return channel.validation_ready
        def __call__(self, contexts, selections, calls, deadline):
            context = contexts[0]
            seen.append(context)
            if context["proposal"]["person"]["nationality"] == "german" or channel.allow:
                return {"nationality": [{"decision": "allow"}]}
            return {"nationality": [{"decision": "deny", "message": "Nationality must match Audi (german)."}]}

    guard = ApproachAdapter(native.PersonnelApproach(state), Channel(), lambda rule, season: {"season": season}, lambda _: None)
    guard.install_prepared()
    yield manager, person, guard, channel, seen, cls
    guard.stop()


def test_approach_refusal_and_adopted_allow_precede_native_records_and_mail(setup):
    manager, person, guard, channel, seen, cls = setup
    escaped = manager.begin_approach
    values = {"context": {"role": "main", "contract_start_week": "53"}}
    assert escaped(" Driver ", person, " Audi ", **values) == (False, "Nationality must match Audi (german).", None)
    assert manager.records == manager.mail == []
    assert escaped("staff", person, "Audi", context={"contract_start_week": "bad"}) == (
        False, "Unable to determine the intended contract season.", None)
    assert len(seen) == 1 and guard.owners == {"nationality": "ready"}
    assert seen[-1]["effective"] == {"season": 2, "start_week": 53, "end_week": None}
    assert "proposed" not in seen[-1]["proposal"]  # Approach has no invented salary/duration.
    person.nationality = "german"
    assert escaped("driver", person, "Audi", **values)[0]
    person.nationality, channel.allow = "australian", True
    assert escaped("staff", person, "Audi", context={"role": "engineer"})[0]
    assert len(manager.records) == len(manager.mail) == 2
    guard.stop()
    assert escaped("driver", person, "Audi", **values)[0]  # Cached wrapper restores native execution.


def test_worker_approach_unavailable_and_foreign_careers_keep_native_ownership(setup):
    manager, person, guard, channel, seen, cls = setup
    guard.personnel = SimpleNamespace(_local=threading.local())
    def approach_on_worker():
        # The existing AI caller owns a copied negotiation manager/state.
        # Its class hook must validate rather than mistaking it for another career.
        draft = copy(manager)
        draft.state = copy(manager.state)
        guard.personnel._local.personnel_caller = {"market": SimpleNamespace(manager=draft)}
        return draft.begin_approach("staff", person, "Audi", initiated_by="ai")
    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(approach_on_worker).result()
    assert result[0] is False and manager.records == manager.mail == []
    assert seen[-1]["initiator"] == "ai"
    channel.validation_ready = False
    assert manager.begin_approach("staff", person, "Audi") == (False, "Validation unavailable", None)
    assert len(seen) == 1 and manager.records == []
    foreign = cls()
    foreign.state, foreign.records, foreign.mail = object(), [], []
    assert foreign.begin_approach("driver", person, "Audi")[0]
    assert foreign.records and manager.records == []
