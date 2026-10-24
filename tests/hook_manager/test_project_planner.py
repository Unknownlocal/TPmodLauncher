# SPDX-License-Identifier: MPL-2.0
"""Planning isolation; native parity uses the owned game probe."""

from copy import deepcopy

import pytest

from hook_manager.manager import HookError
from hook_manager.game_adapters.projects import planner as planner_module
from hook_manager.game_adapters.regulations.contexts import regulation_context
from hook_manager.game_adapters.regulations.project_adapter import ProjectAdapter


class NativeObject:
    pass


def test_incompatible_planning_reports_to_cli_and_log_without_stopping_game(monkeypatch, tmp_path, capsys):
    from launcher.process_lifecycle import reporter

    state = NativeObject()
    state.aero_manager, state.development_manager = NativeObject(), NativeObject()
    monkeypatch.setattr(planner_module, "project_methods", lambda state: {})
    channel = NativeObject()
    channel.bindings = lambda scope: {"owned.rules": ((0, "rule"),)}
    # This case isolates initialization reporting. Native install/rollback is
    # exercised separately by the owned native guard probe.
    monkeypatch.setattr(ProjectAdapter, "__init__", lambda *a: None)
    path = tmp_path / "launch.log"
    with reporter(path) as logger:
        assert ProjectAdapter.create(state, channel, None, logger.warning) is not None
        assert path.read_text(encoding="utf-8") == ""

        def incompatible(state):
            raise HookError("Native project planning helpers changed:\naero")

        monkeypatch.setattr(planner_module, "project_methods", incompatible)
        assert ProjectAdapter.create(state, channel, None, logger.warning) is None
    output = capsys.readouterr().err
    assert "REGULATIONS COMPATIBILITY FAILED" in output
    assert "helpers changed: aero" in output and "continuing native game" in output
    assert path.read_text(encoding="utf-8") == output and len(output.splitlines()) == 1


def test_project_contexts_copy_needed_state_preserve_identity_and_reject_stale_managers(monkeypatch):
    state, aero, chassis = NativeObject(), NativeObject(), NativeObject()
    state.teams = {"team": {"headquarters": {"wind_tunnel": 2}, "hq_upkeep_pct": None,
                            "unrelated": lambda: None, "development": {"current_package_key": "spec_b"}}}
    state.player_team, state.season_no, state.week_index = "team", 3, 4
    state.HQ_DEGRADABLE_BUILDINGS = ("wind_tunnel", "cfd_center")
    state.aero_manager, state.development_manager = aero, chassis
    aero.state = chassis.state = state
    aero.PARTS = ("wing",)
    aero.meta = {"amount_m": 12.0}
    chassis._budget_options = {"modern": {"amount_m": 45.0, "current_amount_m": 40.0}}
    chassis._default_current_package_key = "base"

    def clamp(self, team, *args):
        assert self.state.teams[self.state.player_team] is team
        team["aero"]["projects"]["temporary"] = None
        return 4

    def week(self):
        return (self.state.season_no - 1) * 52 + self.state.week_index

    methods = {
        "state": {"get_hq_upkeep_pct": (lambda *args: 1.0, False)},
        "aero": {"_absolute_week": (week, False), "_clamp_staff": (clamp, False),
                 "_clamp_technical_staff": (clamp, False),
                 "_sanitize_budget_key": (lambda *args: "modern", False),
                 "_budget_meta": (lambda self, *args: self.meta, False)},
        "chassis": {"_absolute_week": (week, False), "_available_staff": (lambda *args: 10, False),
                    "_clamp_staff": (clamp, False), "_clamp_technical_staff": (clamp, False),
                    "_sanitize_budget_key": (lambda *args: "modern", False),
                    "_default_budget_option": (lambda *args: {}, False),
                    "_budget_amount_for_project_type": (lambda meta, period: meta["current_amount_m" if period == "current" else "amount_m"], True),
                    "_sanitize_current_package_key": (lambda self, raw: raw, False),
                    "_current_package_budget_multiplier": (lambda *args: 1.5, False)},
    }
    # The portable fixture supplies checked-helper stand-ins. The developer probe
    # separately runs the strict identity checks and actual native helper bodies.
    monkeypatch.setattr(planner_module, "project_methods", lambda *args: methods)
    planner = planner_module.ProjectPlanner(state)
    before = deepcopy(state.teams)
    aero_args = {"self": aero, "team_name": "team", "part_key": "wing", "staff": 100,
                 "technical_staff": 100, "budget_key": None, "weekly_spend": 999}
    first = planner.prepare("projects.aero.start", aero_args)
    assert first["proposal"]["total_budget_m"] == 12.0 and first["proposal"]["weekly_spend_m"] is None
    assert first["proposal"]["package_key"] is None
    first["proposal"]["employer"]["name"] = "changed by caller"
    assert set(first) == {"clock", "effective", "proposal"}
    spec = regulation_context("development.project", planner.prepare("projects.aero.spec", aero_args),
                              initiator="ai", operation="preview")
    assert spec["schema"] == 1 and spec["initiator"] == "ai" and spec["operation"] == "preview"
    assert spec["proposal"]["action"] == "spec_upgrade" and spec["proposal"]["employer"]["name"] == "team"
    args = {"self": chassis, "team_name": "team", "project_type": "current", "staff": None,
            "technical_staff": None, "budget_key": None, "package_key": None}
    current = planner.prepare("projects.chassis.start", args)
    args["project_type"] = "future"
    future = planner.prepare("projects.chassis.start", args)
    assert current["proposal"]["total_budget_m"] == 60.0 and future["proposal"]["total_budget_m"] == 45.0
    assert current["proposal"]["package_key"] == future["proposal"]["package_key"] == "spec_b"
    assert current["effective"] == {"season": 3, "start_week": 108, "end_week": None}
    assert future["effective"] == {"season": 4, "start_week": 108, "end_week": None}
    assert current["proposal"]["staff"] == current["proposal"]["technical_staff"] == 4
    assert state.teams == before and aero.state is chassis.state is state
    aero.meta["amount_m"] = float("nan")
    with pytest.raises(HookError, match="plain values"):
        planner.prepare("projects.aero.start", aero_args)
    state.aero_manager = NativeObject()
    with pytest.raises(HookError, match="another native state"):
        planner.prepare("projects.chassis.start", args)
