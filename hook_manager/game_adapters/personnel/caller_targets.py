# SPDX-License-Identifier: MPL-2.0
"""Checked shared negotiation callers and driver preparation queries."""

from ..native_methods import native_target, GAME_STATE
from .native_methods import EMAIL, PERSONNEL, PRESTIGE
from ..drivers.native_methods import DRIVER


def personnel_mail_target():
    # Native signers can send through the live mailbox rather than negotiation's
    # copied state. This shared target redirects only the active career's sends.
    return native_target(EMAIL, 'personnel.caller.mail', 'send_email')


def personnel_caller_targets():
    """Return the complete shared driver/staff caller mapping."""
    return (
        native_target(GAME_STATE, 'personnel.caller.rollover', 'finalize_and_rollover_season'),
        native_target(PERSONNEL, 'personnel.caller.submit', 'submit_offer'),
        native_target(PERSONNEL, 'personnel.caller.proposal', 'accept_player_driver_proposal'),
        native_target(PERSONNEL, 'personnel.caller.uncontested', 'sign_uncontested'),
        native_target(PERSONNEL, 'personnel.caller.decision', 'resolve_player_contract_decision'),
        native_target(PERSONNEL, 'personnel.caller.offer', '_execute_offer'),
        native_target(PERSONNEL, 'personnel.caller.due', 'resolve_due_decisions'),
        native_target(PERSONNEL, 'personnel.caller.ai_staff', 'ai_manage_staff'),
        native_target(PERSONNEL, 'personnel.caller.ai_drivers', 'ai_manage_drivers'),
        native_target(PERSONNEL, 'personnel.caller.ai_failure', '_fail_ai_driver_record'),
        native_target(PERSONNEL, 'personnel.caller.finish', '_finish_candidate_market'),
        native_target(PERSONNEL, 'personnel.caller.stage', '_stage_player_contract_confirmation'),
        native_target(PERSONNEL, 'personnel.caller.notice', '_notify_player'),
        native_target(PERSONNEL, 'personnel.caller.debug', '_debug_ai'),
        native_target(DRIVER, 'personnel.caller.active', 'active_contract'),
        native_target(DRIVER, 'personnel.caller.count', 'active_driver_count'),
        native_target(DRIVER, 'personnel.caller.status', 'contract_status_at_week'),
        native_target(DRIVER, 'personnel.caller.entries', 'main_contract_entries_for_week'),
        native_target(DRIVER, 'personnel.caller.next_entries', '_next_season_main_contract_entries'),
        native_target(DRIVER, 'personnel.caller.personalities', 'driver_personalities'),
        native_target(DRIVER, 'personnel.caller.career', 'career_stage_modifiers'),
        native_target(PRESTIGE, 'personnel.caller.prestige', 'displayed_prestige'),
    )
