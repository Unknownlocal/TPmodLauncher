# SPDX-License-Identifier: MPL-2.0
"""Identify the game methods that admit new aero and chassis projects.

These descriptors check compatibility, not regulation conditions. Constructing
them neither installs a wrapper nor makes a mod ready. Context preparation and
caller cancellation must be implemented before these targets enter the bridge.
"""

from ...callables import CallableTarget


PROJECT_GAME_SHA256 = "e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773"

_AERO_SIGNATURE = (
    "(self, team_name: 'str', part_key: 'str', weekly_spend: 'float' = 0.0, "
    "staff: 'int' = 0, technical_staff: 'int' = 0, budget_key: 'Optional[str]' = None) -> 'bool'"
)


def project_targets():
    """Return descriptors without importing the game or gating its whole image."""
    # Resolve by method path/signature, then check the relevant implementation.
    # Unrelated executable changes and source-line shifts are compatible. The
    # executable hash above records the developer inspection's provenance only.
    return (
        CallableTarget("regulations.aero.start", "managers.aero_manager", ("AeroDevelopmentManager",),
                       "start_project", _AERO_SIGNATURE,
                       ("3cc7407b599825997e5e718f5eeb6ee57602ea76b3b2925d7b6422570822c29a",)),
        CallableTarget("regulations.aero.spec", "managers.aero_manager", ("AeroDevelopmentManager",),
                       "start_spec_upgrade", _AERO_SIGNATURE,
                       ("aaf6ac495ff3368312bbcb508356b06ac2b6ccbf2212926b693fba2542cab00e",)),
        CallableTarget("regulations.chassis.start", "managers.development_manager", ("DevelopmentManager",),
                       "start_project",
                       "(self, team_name: str, project_type: str, weekly_spend: Optional[float] = None, "
                       "budget_key: Optional[str] = None, staff: Optional[int] = None, "
                       "technical_staff: Optional[int] = None, concept: Optional[str] = None, "
                       "car_concept: Optional[str] = None, car_focus: Optional[str] = None, "
                       "package_key: Optional[str] = None, allow_ai: bool = False) -> bool",
                       ("738e087be3908e4c98cebc19e03b00036fb186e30b177f9133a6426153ec793a",)),
    )
