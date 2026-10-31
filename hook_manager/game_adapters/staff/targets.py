# SPDX-License-Identifier: MPL-2.0
"""Identify native staff signing without gating unrelated executable contents."""

from ..native_methods import native_target
from .native_methods import STAFF

# Inspection provenance only; runtime compatibility checks the call signature.
STAFF_INSPECTED_SHA256 = 'e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773'


def staff_target():
    return native_target(STAFF, 'staff.sign', 'sign_staff')


def staff_prestige_target():
    return native_target(STAFF, 'staff.prestige-check', 'required_prestige')


def staffing_targets():
    """Common Formula entry points used by loading, rollover and role recovery."""
    return (
        native_target(STAFF, 'staffing.onboard', 'onboard_joining_team_staff'),
        native_target(STAFF, 'staffing.recover', 'ensure_active_teams_have_staff'),
    )
