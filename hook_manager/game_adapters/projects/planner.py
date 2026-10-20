# SPDX-License-Identifier: MPL-2.0
"""Prepare final native project terms for trusted game integrations.

Use the inspected game's staff and budget helpers on private scratch inputs.
Running the actual start or eligibility methods here could initialize projects
or charge money before a trusted caller has accepted the proposal.
"""

from ...manager import HookError, data_copy
from .methods import project_methods
from .snapshot import ProjectSnapshot


class ProjectPlanner:
    def __init__(self, state):
        # Native support raises on incompatibility. The consuming adapter owns
        # reporting and whether to skip its integration; no rule system is needed.
        self.state = state
        self.methods = project_methods(state)
        self.managers = state.aero_manager, state.development_manager

    def prepare(self, target, arguments):
        if target not in ("projects.aero.start", "projects.aero.spec", "projects.chassis.start"):
            raise HookError("Unsupported project proposal context")
        chassis = target == "projects.chassis.start"
        native = self.managers[int(chassis)]
        # Checked helpers belong to this career's managers. A reused planner must
        # not silently plan for a replacement manager or a different live state.
        if (type(arguments) is not dict or arguments.get("self") is not native
                or any(getattr(self.state, name) is not manager for name, manager in
                       zip(("aero_manager", "development_manager"), self.managers))
                or any(manager.state is not self.state for manager in self.managers)):
            raise HookError("Project planner belongs to another native state")
        team_name = arguments["team_name"]
        if type(team_name) is not str or not team_name or type(self.state.teams.get(team_name)) is not dict:
            raise HookError("Project proposal needs an existing named team")
        if type(self.state.season_no) is not int or type(self.state.week_index) is not int:
            raise HookError("Project proposal needs the native career clock")
        scratch = ProjectSnapshot(self.state, team_name, self.methods)
        manager = scratch.state.development_manager if chassis else scratch.state.aero_manager
        team = scratch.team
        if chassis:
            period = arguments["project_type"]
            if period not in ("current", "future"):
                raise HookError("Unknown chassis project period")
            capacity = manager._available_staff(team, period)
            staff = arguments["staff"]
            if staff is None:
                # Match the native default before clamping; mods receive the
                # actual allocation rather than an omitted or oversized request.
                staff = max(0, int(round(capacity * .6)))
            staff = manager._clamp_staff(team, period, staff)
            technical = manager._clamp_technical_staff(team, period, arguments["technical_staff"] if arguments["technical_staff"] is not None else 0)
            budget = manager._sanitize_budget_key(arguments["budget_key"])
            meta = manager._budget_options.get(budget, manager._default_budget_option())
            amount = manager._budget_amount_for_project_type(meta, period)
            package = manager._sanitize_current_package_key(
                arguments["package_key"] if arguments["package_key"] is not None else
                team["development"].get("current_package_key", manager._default_current_package_key))
            if period == "current":
                # Native future budgets do not apply the current-package uplift.
                amount *= float(manager._current_package_budget_multiplier({}, package))
            project_key = period
            action = "start"
        else:
            package = None
            period, project_key = "current", arguments["part_key"]
            if type(project_key) is not str or project_key not in native.PARTS:
                raise HookError("Unknown aero project part")
            staff = manager._clamp_staff(team, int(arguments["staff"]), project_key)
            technical = manager._clamp_technical_staff(team, int(arguments["technical_staff"]), project_key)
            spec = target == "projects.aero.spec"
            kind = "spec" if spec else "development"
            budget = manager._sanitize_budget_key(arguments["budget_key"], kind, arguments["weekly_spend"])
            meta = manager._budget_meta(budget, kind)
            amount = float(meta.get("amount_m", 0.0) or 0.0)
            action = "spec_upgrade" if spec else "start"
        if type(staff) is not int or type(technical) is not int:
            raise HookError("Native project staff counts must be integers")
        week = manager._absolute_week()
        # The managers use different week offsets. Keep their own calculation;
        # future chassis rules apply to the next season despite creation today.
        # Legacy weekly_spend only selects an aero tier and is discarded for
        # chassis. Neither start stores it as a promised weekly expenditure.
        # created_week is available now; no scheduled end exists at admission.
        # Keep native proposal data independent of regulation schema/operation
        # metadata so other trusted game adapters can use the same preparation.
        return data_copy({
            "clock": {"season": scratch.state.season_no, "absolute_week": week},
            "effective": {"season": scratch.state.season_no + int(period == "future"), "start_week": week, "end_week": None},
            "proposal": {"employer": {"ref": scratch.reference, "name": team_name, "kind": "team"},
                         "family": "chassis" if chassis else "aero", "action": action, "project_key": project_key,
                         "design_period": period, "package_key": package, "budget_key": budget, "weekly_spend_m": None,
                         "total_budget_m": amount, "staff": staff, "technical_staff": technical},
        })
