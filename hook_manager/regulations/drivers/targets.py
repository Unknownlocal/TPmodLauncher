# SPDX-License-Identifier: MPL-2.0
"""Identify driver signing by signature and relevant implementation, not image hash."""

from ...callables import CallableTarget

# Developer inspection provenance only; runtime checks the methods themselves.
DRIVER_INSPECTED_SHA256 = 'e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773'


def driver_target():
    return CallableTarget("regulations.driver.sign", "managers.driver", ("DriverManager",),
                          "sign_driver", "(self, team_name: str, driver: managers.driver.Driver, salary_m: float, years: int, cost_points: int = 0, role: str = 'main', priority: str = 'equal', bonuses: Optional[Dict[str, float]] = None, entry_id: Optional[str] = None, contract_start_week: Optional[int] = None) -> bool",
                          ('83d99f75a2496f2c37ffbd355d228d48126533d73ff199f82781be3a659a09dc',))
