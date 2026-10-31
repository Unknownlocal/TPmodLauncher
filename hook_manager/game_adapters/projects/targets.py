# SPDX-License-Identifier: MPL-2.0
"""Identify the game methods that admit new aero and chassis projects.

These descriptors check compatibility, not regulation conditions. Constructing
them neither installs a wrapper nor makes a mod ready. The project adapter owns
context preparation, caller cancellation and wrapper installation.
"""

from ..native_methods import native_target
from .native_methods import AERO, CHASSIS


PROJECT_GAME_SHA256 = "e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773"


def project_targets():
    """Return descriptors without importing the game or gating its whole image."""
    # Resolve by method path/signature. Body edits and source-line shifts do not
    # disable support; the executable hash records inspection provenance only.
    return (
        native_target(AERO, 'projects.aero.start', 'start_project'),
        native_target(AERO, 'projects.aero.spec', 'start_spec_upgrade'),
        native_target(CHASSIS, 'projects.chassis.start', 'start_project'),
    )
