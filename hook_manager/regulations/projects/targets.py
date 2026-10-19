# SPDX-License-Identifier: MPL-2.0
"""Identify the game methods that admit new aero and chassis projects.

These descriptors check compatibility, not regulation conditions. Constructing
them neither installs a wrapper nor makes a mod ready. Context preparation and
caller cancellation must be implemented before these targets enter the bridge.
"""

from ...callables import CallableTarget
from ...manager import HookError


PROJECT_GAME_SHA256 = "e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773"

_AERO_SIGNATURE = (
    "(self, team_name: 'str', part_key: 'str', weekly_spend: 'float' = 0.0, "
    "staff: 'int' = 0, technical_staff: 'int' = 0, budget_key: 'Optional[str]' = None) -> 'bool'"
)


def project_targets(game_sha256):
    """Return fresh descriptors for the inspected build, without importing the game."""
    if game_sha256 != PROJECT_GAME_SHA256:
        raise HookError("Development-project mappings do not support this game build")
    # Pin bodies as well as signatures: a later build can retain its arguments
    # while changing normalization or moving a write ahead of our admission point.
    return (
        CallableTarget("regulations.aero.start", "managers.aero_manager", ("AeroDevelopmentManager",),
                       "start_project", _AERO_SIGNATURE,
                       ("b7b0550d0af243ff34bc90af50cab613fa9c2bc926d3d1f664d014139adb484a",)),
        CallableTarget("regulations.aero.spec", "managers.aero_manager", ("AeroDevelopmentManager",),
                       "start_spec_upgrade", _AERO_SIGNATURE,
                       ("a96539c3c42376789d9f0dd860bc6017fc8ed80e74309f259d69419d5a376cde",)),
        CallableTarget("regulations.chassis.start", "managers.development_manager", ("DevelopmentManager",),
                       "start_project",
                       "(self, team_name: str, project_type: str, weekly_spend: Optional[float] = None, "
                       "budget_key: Optional[str] = None, staff: Optional[int] = None, "
                       "technical_staff: Optional[int] = None, concept: Optional[str] = None, "
                       "car_concept: Optional[str] = None, car_focus: Optional[str] = None, "
                       "package_key: Optional[str] = None, allow_ai: bool = False) -> bool",
                       ("814d8a40e05673b7963c02cc0d1277b54e3032da446dd03b98edc82cbdda61a3",)),
    )
