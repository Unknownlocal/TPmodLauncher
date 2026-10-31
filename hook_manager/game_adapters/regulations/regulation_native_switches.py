# SPDX-License-Identifier: MPL-2.0
"""Install the two native check switches from booleans or rule-option references."""

from ...diagnostics import build_log_record

from ...manager import HookError
from ...callables import method_group
from ..drivers.rookie_switch import RookieContractLengthSwitch
from ..staff.prestige_switch import StaffPrestigeSwitch
from .regulation_selection import RegulationSelection


def _rookie_query(state, selection):
    """Infer a query's contract season when no enclosing proposal supplies it."""
    start = method_group(state.driver_manager, {"_negotiation_start_week":
        "(self, team_name: str, driver: managers.driver.Driver, role: str = 'main') -> int"})["_negotiation_start_week"][0]
    def enabled(team_name, driver, role):
        season = None
        if selection.scoped_season() is None and selection.varies_by_season():
            week = start(state.driver_manager, team_name, driver, role)
            season = state.season_no + int(week >= state.next_season_start())
        return selection.enabled(season)
    return enabled


def install_regulation_switches(state, values, *, submit_log):
    """Return owned hooks for trusted setup to stop on policy/career removal.

    No file paths, author functions or game-method names are accepted. This
    helper alone does not declare a policy ready or enable launcher admission.
    """
    if (type(values) is not dict or values.keys() - {"rookie_contract_length_check", "staff_prestige_check"}
            or any(type(value) is not bool and not RegulationSelection.binding(value) for value in values.values())):
        raise HookError("Expected native regulation check booleans or rule references")
    # Validate the whole record before touching either class. Missing or true
    # installs nothing; false disables that one native restriction. A reference
    # installs a selector so later adopted options can change that decision.
    hooks = []
    try:
        for name, factory in (("rookie_contract_length_check", RookieContractLengthSwitch),
                              ("staff_prestige_check", StaffPrestigeSwitch)):
            enabled = values.get(name, True)
            if type(enabled) is dict:
                selection = RegulationSelection(state, enabled)
                if name == "rookie_contract_length_check":
                    try:
                        enabled = _rookie_query(state, selection)
                    except Exception as error:
                        submit_log(build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED drivers.rookie-check: "
                               + str(error) + "; rookie check switch skipped; continuing native game", error=error, component="regulations"))
                        continue
                else:
                    enabled = selection.enabled
            hook = factory.create(state, enabled=enabled, submit_log=submit_log)
            if hook is not None:
                hooks.append(hook)
        return tuple(hooks)
    except BaseException:
        for hook in reversed(hooks):
            hook.stop()
        raise
