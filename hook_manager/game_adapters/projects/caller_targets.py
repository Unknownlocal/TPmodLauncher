# SPDX-License-Identifier: MPL-2.0
"""Checked callers and helpers whose preparation must precede project admission.

Compatibility uses method paths and signatures. The AI chassis caller selects
its package before start; the start itself stores it
on success, so the earlier write can be suppressed within that checked caller.
"""

from ..native_methods import native_target
from .native_methods import AERO_SCREEN, CHASSIS_SCREEN, AI_DEVELOPMENT, AERO, CHASSIS


def project_caller_targets():
    return (
        native_target(AERO_SCREEN, 'projects.caller.aero_ui', '_execute_action'),
        native_target(CHASSIS_SCREEN, 'projects.caller.chassis_ui', '_handle_button'),
        native_target(AI_DEVELOPMENT, 'projects.caller.ai_current', '_ensure_current_project'),
        native_target(AI_DEVELOPMENT, 'projects.caller.ai_future', '_ensure_future_project'),
        native_target(AI_DEVELOPMENT, 'projects.caller.ai_aero', '_ensure_aero_projects'),
        native_target(AERO, 'projects.caller.aero_ai', '_ai_try_launch_project'),
        native_target(CHASSIS, 'projects.caller.package', 'set_current_package_choice'),
        native_target(AERO, 'projects.caller.aero_parts', '_part_record'),
        native_target(AERO, 'projects.caller.aero_store', '_project_store'),
        native_target(CHASSIS, 'projects.caller.chassis_store', '_project_store'),
        native_target(AI_DEVELOPMENT, 'projects.caller.ai_project', '_dev_project'),
    )
