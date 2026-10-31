# SPDX-License-Identifier: MPL-2.0
"""Staff rosters and validation coordinators used by staffing tests."""

import inspect
import sys
import threading
from types import ModuleType, SimpleNamespace

from hook_manager.callables import CallBinding, CallableTarget, refuse_boolean
from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.regulation_personnel import RegulationPersonnelAdapter
from hook_manager.game_adapters.regulations import regulation_personnel as implementation
from hook_manager.game_adapters.regulations.staffing_coordinator import StaffingCoordinator
from hook_manager.game_adapters.staff import planner as contracts, staffing
from hook_manager.game_adapters.staff.staffing import StaffingPlanner


def staffing_state(monkeypatch):
    """Create a fresh roster and native staffing methods without installing hooks."""
    class Manager:
        def _create_free_staff_for_role(self, role, rng):
            person = SimpleNamespace(name="Generated", role=role, nationality="GB", age=35,
                                     contract=None, future_contract=None, morale_effects=[], retired=False)
            self.staff.append(person)
            self.staff_season_history[person.name] = []
            return person

        def onboard_joining_team_staff(self, team_name, *, contract_years=1):
            if self.team_has_active_staff(team_name, "chief_designer", week=114):
                return []
            person = next((person for person in self.staff if not person.retired and not person.contract), None)
            if person is None:
                person = self._create_free_staff_for_role("chief_designer", None)
            ok = self.sign_staff(team_name, person, 2., contract_years, signer_kind="team",
                                 bypass_requirements=True, allow_current_cover=True)
            return [person.name] if ok else []

        def ensure_active_teams_have_staff(self, roles=None):
            for name in self.state.teams:
                self.onboard_joining_team_staff(name)

        def sign_staff(self, team_name, staff, salary_m, years, *, bonuses=None, signer_kind="team",
                       bypass_requirements=False, allow_current_cover=False, contract_start_week=None):
            if self.refuse:
                return False
            staff.contract = {"team": team_name}
            staff.morale_effects.append({"amount": 1})
            return True

        def team_has_active_staff(self, team_name, role, week=None):
            return any(getattr(person, "role", None) == role and (person.contract or {}).get("team") == team_name
                       for person in self.staff)

    manager = Manager()
    manager.refuse = False
    person = SimpleNamespace(name="Existing", role="chief_designer", nationality="GB", age=40,
                             contract=None, future_contract=None, morale_effects=[], retired=False)
    manager.staff = [person]
    manager.staff_season_history = {person.name: [{"season": 2}]}
    manager._name_pool = SimpleNamespace(first_names=["Owned"])
    state = SimpleNamespace(game_mode="formula", season_no=3, week_index=9,
                            teams={"first": {"active": True}, "second": {"active": True}}, staff_manager=manager)
    manager.state = state
    state.current_season_start_week = lambda: 105
    state.next_season_start = lambda: 157
    queries = {
        "state": {"current_season_start_week": (lambda self: 105, False), "next_season_start": (lambda self: 157, False)},
        "staff": {"normalize_role": (lambda role: role, True),
                  "team_has_active_staff": (lambda self, *args, **kwargs: False, False)},
    }
    monkeypatch.setattr(contracts, "staff_methods", lambda state: queries)
    methods = {name: (getattr(Manager, name), False) for name in (
        "_create_free_staff_for_role", "onboard_joining_team_staff", "ensure_active_teams_have_staff", "team_has_active_staff")}
    monkeypatch.setattr(staffing, "staffing_methods", lambda manager: {"staff": methods, "names": {}})
    return state, manager, person


def staffing_coordinator(monkeypatch, mode="allow", *, callers=False):
    """Create a coordinator; callers=True also wraps the native staffing callers.

    The test must stop the returned guard in either case.
    """
    state, manager, person = staffing_state(monkeypatch)
    planner = StaffingPlanner(state)

    class Channel:
        def __init__(self):
            self.epoch, self.closed, self.mode = 1, False, mode
            self._lock = threading.Lock()
            self._generations = dict.fromkeys(("owned.rules", "peer.rules"), 1)
            self._owners = {owner: {"revision": "owned"} for owner in self._generations}
            self._unavailable, self.actions, self.calls = set(), [], []
            self.after = lambda: None

        def bindings(self, scope):
            return {owner: ((0, "rule"),) for owner in self._owners}

        def __call__(self, contexts, selections, calls, deadline):
            assert contexts[0]["initiator"] in ("system", "unknown") and contexts[0]["operation"] == "commit"
            self.actions.append(contexts[0])
            self.calls.append(calls)
            if self.mode == "unavailable":
                self._unavailable.add("owned.rules")
                raise RuntimeError("Owned unavailable handler")
            result = {owner: [{"decision": "allow"} if self.mode == "allow" else
                              {"decision": "deny", "message": "Owned policy refusal"}] for owner in calls}
            self.after()
            return result

    class Guards(RegulationPersonnelAdapter):
        def __init__(self, channel):
            RegulationGuards.__init__(self, channel, lambda *args: {"selected": "current"}, "personnel.contract")
            self.planners, self.feedback = {"staff": planner.contracts}, None
            self._personnel_operation = threading.Lock()

    channel = Channel()
    module = ModuleType("owned_staffing_coordinator")
    module.StaffManager = type(manager)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    signing = CallableTarget("staff.sign", module.__name__, ("StaffManager",), "sign_staff", str(inspect.signature(planner.sign)))
    if callers:
        targets = tuple(CallableTarget("staffing." + role, module.__name__, ("StaffManager",), method,
                                      str(inspect.signature(getattr(type(manager), method))))
                        for role, method in (("onboard", "onboard_joining_team_staff"), ("recover", "ensure_active_teams_have_staff")))
        class DriverManager:
            def sign_driver(self, driver):
                return True

        class Negotiations:
            def get_record(self, candidate_type: 'str', candidate: 'Any', team_name: 'Optional[str]', *, signer_kind: 'str' = 'team') -> 'Optional[Dict[str, Any]]':
                return None

        state.personnel_negotiation_manager = Negotiations()
        module.DriverManager = DriverManager
        driver = DriverManager()
        driver.state = state
        state.driver_manager = driver
        monkeypatch.setattr(implementation, "DriverContractPlanner", lambda state:
                            SimpleNamespace(state=state, manager=driver))
        monkeypatch.setattr(implementation, "driver_target", lambda: CallableTarget(
            "drivers.sign", module.__name__, ("DriverManager",), "sign_driver", "(self, driver)"))
        monkeypatch.setattr(implementation, "StaffingPlanner", lambda state: planner)
        monkeypatch.setattr(implementation, "staff_target", lambda: signing)
        monkeypatch.setattr(implementation, "staffing_targets", lambda: targets)
        monkeypatch.setattr(implementation, "personnel_caller_targets", lambda: ())
        monkeypatch.setattr(implementation, "check_preparation", lambda *args: None)
        guards = RegulationPersonnelAdapter(state, channel, lambda *args: {"selected": "current"}).install_prepared()
        assert guards.staffing is not None
        return state, manager, person, channel, guards, guards.staffing
    guards = Guards(channel)
    guards.approve(signing)
    project = guards._projection
    for owner in channel._owners:
        guards.register(owner, [CallBinding("staff", "staff.sign", project, refuse_boolean)])
    return state, manager, person, channel, guards, StaffingCoordinator(guards, planner)

