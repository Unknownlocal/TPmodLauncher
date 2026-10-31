# SPDX-License-Identifier: MPL-2.0
"""Plan required staff generation and contracts without changing the live roster."""

from copy import copy, deepcopy
import inspect
from types import MethodType

from ...manager import HookError
from .planner import StaffContractPlanner
from .staff_signing import StaffSigning
from .staffing_methods import staffing_methods
from .staffing_plan import StaffingPlan
from .staffing_publication import StaffingPublication


class StaffingPlanner:
    """Run native staffing privately; an optional trusted callback checks contracts."""
    def __init__(self, state):
        self.contracts = StaffContractPlanner(state)
        self.state, self.manager = state, state.staff_manager
        self.methods = staffing_methods(self.manager)
        # Retain native behavior before guards acquire the method. A later plan
        # calls it only on its private manager; the live guard remains installed.
        self.sign = inspect.getattr_static(self.manager, "sign_staff")
        self.signature = inspect.signature(self.sign)

    def prepare_onboarding(self, team_name, *, contract_years=1, validate=None, payroll=False):
        """Prepare native joining-team staff choices and their final cover terms."""
        return self._prepare("onboard_joining_team_staff", team_name, contract_years=contract_years,
                             validate=validate, payroll=payroll)

    def prepare_recovery(self, roles=None, *, validate=None, payroll=False):
        """Prepare missing team staff and native population seeds for these roles."""
        return self._prepare("ensure_active_teams_have_staff", roles, validate=validate, payroll=payroll)

    def _prepare(self, method, *args, validate=None, payroll=False, **kwargs):
        if self.state.staff_manager is not self.manager or self.manager.state is not self.state:
            raise HookError("Staffing planner belongs to another native state")
        # Capture the baseline before copying roots. Otherwise an old private
        # roster could accidentally be checked against a newer live snapshot.
        publication = StaffingPublication(self.state)
        state, manager = copy(self.state), copy(self.manager)
        state.staff_manager, manager.state = manager, state
        # Queries only read existing people, teams, histories and name lists. Copy
        # their roster/root containers, not every person and historical season.
        manager.staff = list(self.manager.staff)
        manager.staff_season_history = dict(self.manager.staff_season_history)
        manager._name_pool = copy(self.manager._name_pool)
        for owner, methods in ((manager, self.methods["staff"]), (manager._name_pool, self.methods["names"])):
            for name, (function, static) in methods.items():
                setattr(owner, name, function if static else MethodType(function, owner))
        contracts = copy(self.contracts)
        contracts.state, contracts.manager = state, manager
        generated, signings = [], []
        requirements = set()
        active_staff = manager.team_has_active_staff

        def team_has_staff(team_name, role, week=None):
            # Ask native code to define required slots. Its recovery loop ignores
            # sign_staff False, so the final plan checks these slots explicitly.
            if week is None:
                week = state.current_season_start_week() + state.week_index
            requirements.add((team_name, role, week))
            return active_staff(team_name, role, week=week)

        manager.team_has_active_staff = team_has_staff
        live_ids = {id(person) for person in self.manager.staff}
        create = manager._create_free_staff_for_role

        def create_staff(*args, **kwargs):
            person = create(*args, **kwargs)
            if id(person) in live_ids or not any(member is person for member in manager.staff):
                raise HookError("Native staffing generation did not return a new roster member")
            generated.append(person)
            return person

        def sign_staff(*args, **kwargs):
            bound = self.signature.bind(manager, *args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            # Recovery must bypass prestige checks: evaluating them could
            # change the borrowed team records during private preparation.
            if values["signer_kind"] != "team" or values["bypass_requirements"] is not True:
                raise HookError("Staffing preparation requires native team recovery arguments")
            person = values["staff"]
            if id(person) not in publication.people:
                if not any(member is person for member in manager.staff):
                    raise HookError("Native staffing selected a person outside its roster")
                # Copy a selected person once. Later team choices see its private
                # agreement, while canonical existing/generated people stay intact.
                original, person = publication.person(person)
                manager.staff = [person if member is original else member for member in manager.staff]
                # Native callers can reuse an earlier argument or read its new
                # roster entry. Both identities must resolve to this same copy.
            else:
                original, person = publication.person(person)
            values["staff"] = person
            # This planner is bound to the evolving private roster. Earlier
            # accepted hires therefore contribute to the next candidate's total.
            checks = [] if payroll else None
            prepared = contracts.prepare(dict(values), **({"payroll_checks": checks} if payroll else {}))
            # Terms come from the temporary person; identity belongs to the live
            # person or the retained unsigned generator result, never that copy.
            prepared["proposal"]["person"]["ref"] = "staff:" + str(id(original))
            arguments = {**values, "self": self.manager, "staff": original, "bonuses": deepcopy(values["bonuses"])}
            # The callback can withhold execution. Allowed native effects stay
            # private so later choices see the roster native signing would leave.
            # None records withheld execution separately from native False.
            accepted = self.sign(*bound.args, **bound.kwargs) if validate is None or validate(
                prepared, **({"input_checks": checks} if payroll else {})) else None
            signings.append(StaffSigning(arguments, prepared, accepted))
            return False if accepted is None else accepted

        manager._create_free_staff_for_role, manager.sign_staff = create_staff, sign_staff
        result = getattr(manager, method)(*args, **kwargs)
        # Existing history lists were read-only inputs. Only new keys belong to
        # the generated population a later coordinator might publish.
        history = {name: rows for name, rows in manager.staff_season_history.items()
                   if name not in self.manager.staff_season_history}
        # Use the saved native query, not the recorder, when checking completion.
        missing = tuple(slot for slot in sorted(requirements) if not active_staff(slot[0], slot[1], week=slot[2]))
        return StaffingPlan(result, tuple(signings), tuple(generated), history, missing, publication)
