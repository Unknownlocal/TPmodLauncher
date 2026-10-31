# SPDX-License-Identifier: MPL-2.0
"""Negotiation state and signing stand-ins shared by personnel tests."""

import inspect
import sys
from types import ModuleType, SimpleNamespace

from hook_manager.callables import CallableTarget
from hook_manager.game_adapters.regulations import regulation_personnel as implementation


def negotiation_state(monkeypatch):
    """Create native negotiation stand-ins and planners without installing an adapter."""
    module = ModuleType("owned_personnel_adapter")
    monkeypatch.setitem(sys.modules, module.__name__, module)

    class DriverManager:
        def sign_driver(self, driver, salary=2):
            self.state.writes.append(salary)
            if salary == 0:
                raise ValueError("Native failure")
            if self.state.native_refuse:
                return False
            driver.contract = {"salary": salary}
            mail = getattr(self.state, "email_manager", None)
            if mail is not None:
                mail.send_email("Native signed", "Owned contract")
            return True

        def driver_personalities(self, driver):
            driver.personality = "loyal"
            driver.personalities = ["loyal"]
            return driver.personalities

        def career_stage_modifiers(self, driver):
            driver.career_stage = "youngster"
            return {"key": driver.career_stage}

    # These stand-ins fill lazy prestige/personality fields so the test can
    # check that those writes stay private until publication.
    class PrestigeManager:
        def displayed_prestige(self, team):
            team["prestige"] = 50
            return 50

    class StaffManager:
        def onboard_joining_team_staff(self, team_name):
            return []

        def ensure_active_teams_have_staff(self):
            return None

        def sign_staff(self, staff, salary=2):
            self.state.prestige_manager.displayed_prestige(self.state.teams["target"])
            self.state.writes.append("staff")
            if self.state.native_refuse:
                return False
            staff.contract = {"salary": salary}
            return True

    class Negotiations:
        def get_record(self, candidate_type: 'str', candidate: 'Any', team_name: 'Optional[str]', *, signer_kind: 'str' = 'team') -> 'Optional[Dict[str, Any]]':
            return self.row

        def submit_offer(self, candidate_type, candidate, team_name, offer, score, *, signer_kind="team", attempts_used=None, annoyed=None, finalize=False):
            self.row.update(offer=offer, offer_score=score, status="final_offer" if finalize else "offer_accepted")
            self.candidate_deadlines["candidate"] = 125
            self.state.writes.append("submitted")
            return True, "Offer submitted", self.row

        def _execute_offer(self, row, candidate):
            if row.get("native_precheck_failure"):
                return False
            if row["candidate_type"] == "staff":
                self.state.prestige_manager.displayed_prestige(self.state.teams["target"])
                if row.get("context", {}).get("entry_id"):
                    candidate._oval_target_entry_id = row["context"]["entry_id"]
                return self.state.staff_manager.sign_staff(candidate)
            self.state.driver_manager.driver_personalities(candidate)
            self.state.driver_manager.career_stage_modifiers(candidate)
            self.state.prestige_manager.displayed_prestige(self.state.teams["target"])
            row["offer"]["years"] = 1
            signed = self.state.driver_manager.sign_driver(candidate)
            if not signed:
                row["execution_block_reason"] = "Native final validation failed"
            return signed

        def resolve_player_contract_decision(self, decision_id, accept):
            signed = self._execute_offer(self.row, self.driver)
            if not signed:
                self.state.writes.append("backup")
            self.pending.pop(decision_id)
            return True, "closed"

        def resolve_due_decisions(self):
            for row_ids, candidate in self.markets:
                for row_id in row_ids:
                    if getattr(self.state, "email_manager", None) is not None:
                        self._notify_player(self.records[row_id], "Preparing " + row_id, "Owned preparation")
                    self.records[row_id]["prepared"] = True
                    if self._execute_offer(self.records[row_id], candidate):
                        break
                self._finish_candidate_market(self.records[row_ids[0]], None)

        def _finish_candidate_market(self, source_row, winner):
            self.state.writes.append("closed")
            source_row["status"] = "completed"
            self.candidate_deadlines.pop(source_row.get("candidate_key"), None)
            if getattr(self.state, "email_manager", None) is not None:
                self.state.email_manager.send_email("Market completed", "Owned result")

        def _notify_player(self, row, subject, body):
            self.state.email_manager.send_email(subject, body)

        def sign_uncontested(self, candidate_type, candidate, team_name, *, signer_kind="team"):
            ok = self._execute_offer(self.row, candidate)
            return ok, "Native driver will not renew"

        def ai_manage_drivers(self, role_phase="all"):
            ok, reason = self.sign_uncontested("driver", self.driver, "target")
            if not ok:
                self._fail_ai_driver_record(self.row, reason, permanent="will not renew" in reason.lower())

        def _fail_ai_driver_record(self, row, reason, *, permanent=False):
            row.update(failure_reason=reason, permanent_block=permanent,
                       blocked_role="reserve" if not permanent and "reserve role" in reason.lower() else None)
            self.state.writes.append("cooldown")

    class EmailManager:
        def __init__(self, state):
            self.state, self.inbox, self.urgent_sound_serial = state, [], 0

        def send_email(self, subject, body):
            self.inbox.insert(0, {"subject": subject, "body": body})
            self.urgent_sound_serial += 1

    class GameState(SimpleNamespace):
        def finalize_and_rollover_season(self):
            for person in self.rollover_candidates:
                if self.driver_manager.sign_driver(person):
                    self.rollover_signed = person
                    break
            self.rollover_complete = True

    module.GameState = GameState
    module.DriverManager, module.Negotiations, module.PrestigeManager = DriverManager, Negotiations, PrestigeManager
    module.StaffManager = StaffManager
    module.EmailManager = EmailManager
    state = GameState(writes=[], teams={"target": {}}, prestige_manager=PrestigeManager(), native_refuse=False)
    manager, negotiations = DriverManager(), Negotiations()
    manager.state = negotiations.state = state
    state.driver_manager, state.personnel_negotiation_manager = manager, negotiations
    state.staff_manager = StaffManager()
    state.staff_manager.state = state
    driver = SimpleNamespace(contract=None)
    state.rollover_candidates = [driver]
    negotiations.row = {"candidate_type": "driver", "team_id": "target", "initiated_by": "player", "offer": {"years": 3}}
    negotiations.driver, negotiations.pending = driver, {"decision": {"status": "pending"}}
    negotiations.records = {"row": negotiations.row}
    negotiations.pending_player_decisions = negotiations.pending
    negotiations.driver_proposals, negotiations.candidate_deadlines = {}, {}
    negotiations._serial = negotiations._proposal_serial = 0

    def target(name, owner, method):
        fn = getattr(owner, method)
        return CallableTarget(name, module.__name__, (owner.__name__,), method,
                              str(inspect.signature(fn)))

    signing = target("drivers.sign", DriverManager, "sign_driver")
    callers = (target("personnel.caller.rollover", GameState, "finalize_and_rollover_season"),
               target("personnel.caller.submit", Negotiations, "submit_offer"),
               target("personnel.caller.offer", Negotiations, "_execute_offer"),
               target("personnel.caller.decision", Negotiations, "resolve_player_contract_decision"),
               target("personnel.caller.due", Negotiations, "resolve_due_decisions"),
               target("personnel.caller.uncontested", Negotiations, "sign_uncontested"),
               target("personnel.caller.ai_drivers", Negotiations, "ai_manage_drivers"),
               target("personnel.caller.ai_failure", Negotiations, "_fail_ai_driver_record"),
               target("personnel.caller.finish", Negotiations, "_finish_candidate_market"),
               target("personnel.caller.notice", Negotiations, "_notify_player"),
               target("personnel.caller.personalities", DriverManager, "driver_personalities"),
               target("personnel.caller.career", DriverManager, "career_stage_modifiers"),
               target("personnel.caller.prestige", PrestigeManager, "displayed_prestige"))
    monkeypatch.setattr(implementation, "driver_target", lambda: signing)
    monkeypatch.setattr(implementation, "staff_target", lambda: target("staff.sign", StaffManager, "sign_staff"))
    monkeypatch.setattr(implementation, "personnel_caller_targets", lambda: callers)
    monkeypatch.setattr(implementation, "personnel_mail_target", lambda: target("personnel.caller.mail", EmailManager, "send_email"))
    monkeypatch.setattr(implementation, "check_preparation", lambda state: None)

    class Channel:
        mode = "deny"
        message = "Owned refusal"

        def __init__(self):
            self._unavailable = set()
            self.contexts = []

        def bindings(self, scope):
            return {"owned.rules": ((0, "rule"),)}

        def __call__(self, contexts, selections, calls, deadline):
            assert contexts[0]["schema"] == 1 and contexts[0]["scope"] == "personnel.contract"
            assert contexts[0]["operation"] in ("commit", "preview")
            self.contexts.extend(contexts)
            if self.mode == "failed":
                self._unavailable.add("owned.rules")
                raise RuntimeError("Owned handler failed")
            return {"owned.rules": [{"decision": "allow"} if self.mode == "allow"
                                    else {"decision": "deny", "message": self.message}]}

    planner = SimpleNamespace(state=state, manager=manager,
                              prepare=lambda arguments: {"clock": {"season": 3, "absolute_week": 114},
                                                         "effective": {"season": 3}, "proposal": {}})
    staff_planner = SimpleNamespace(state=state, manager=state.staff_manager, prepare=planner.prepare)
    staffing = SimpleNamespace(state=state, manager=state.staff_manager, contracts=staff_planner,
                               sign=StaffManager.sign_staff)
    monkeypatch.setattr(implementation, "DriverContractPlanner", lambda state: planner)
    monkeypatch.setattr(implementation, "StaffingPlanner", lambda state: staffing)
    monkeypatch.setattr(implementation, "staffing_targets", lambda: (
        target("staffing.onboard", StaffManager, "onboard_joining_team_staff"),
        target("staffing.recover", StaffManager, "ensure_active_teams_have_staff")))
    return state, driver, negotiations, planner, Channel(), signing, callers


def submitted_negotiation(monkeypatch):
    """Install offer validation without executing a signer; the caller stops the adapter."""
    state, person, negotiations, planner, channel, _, _ = negotiation_state(monkeypatch)
    state.season_no, state.next_season_start = 3, lambda: 157
    state.current_season_start_week = lambda: 105
    negotiations.row.update(status="talks_open", context={"contract_start_week": 157})
    def signing(self, team_name, driver, salary_m, years, *, bonuses=None, role="main", priority="equal", contract_start_week=None):
        pass  # Signature only; no signer is executed during submission.
    planner.prepare = lambda arguments: {
        "clock": {"season": 3, "absolute_week": 114},
        "effective": {"season": 4, "start_week": 157, "end_week": 157 + 52 * arguments["years"]},
        "proposal": {"proposed": {"salary_m": arguments["salary_m"], "duration_weeks": 52 * arguments["years"]}},
    }
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    adapter.offers.signatures["driver"] = inspect.signature(signing)
    return state, person, negotiations, channel, adapter

