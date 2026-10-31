# SPDX-License-Identifier: MPL-2.0
"""Identify the game methods that admit new aero and chassis projects.

These descriptors check compatibility, not regulation conditions. Constructing
them neither installs a wrapper nor makes a mod ready. The project adapter owns
context preparation, caller cancellation and wrapper installation.
"""

from ..native_methods import native_target
from .native_methods import AERO, CHASSIS


def project_targets():
    """Return method-path and signature checks without importing the game."""
    return (
        native_target(AERO, 'projects.aero.start', 'start_project'),
        native_target(AERO, 'projects.aero.spec', 'start_spec_upgrade'),
        native_target(CHASSIS, 'projects.chassis.start', 'start_project'),
    )
