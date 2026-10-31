# SPDX-License-Identifier: MPL-2.0
"""Identify staff signing by its native path and call signature."""

from ..native_methods import native_target
from .native_methods import STAFF


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
