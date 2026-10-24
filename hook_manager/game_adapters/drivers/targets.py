# SPDX-License-Identifier: MPL-2.0
"""Identify driver signing by its native path and call signature."""

from ...callables import CallableTarget

# Developer inspection provenance only; never a runtime executable/body gate.
DRIVER_INSPECTED_SHA256 = 'e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773'


def driver_target():
    return CallableTarget("drivers.sign", "managers.driver", ("DriverManager",),
                          "sign_driver", "(self, team_name: str, driver: managers.driver.Driver, salary_m: float, years: int, cost_points: int = 0, role: str = 'main', priority: str = 'equal', bonuses: Optional[Dict[str, float]] = None, entry_id: Optional[str] = None, contract_start_week: Optional[int] = None) -> bool")


def rookie_check_target():
    return CallableTarget("drivers.rookie-check", "managers.driver", ("DriverManager",),
                          "formula_rookie_contract_year_limit",
                          "(self, team_name: str, driver: managers.driver.Driver, role: str = 'main') -> Optional[int]")
