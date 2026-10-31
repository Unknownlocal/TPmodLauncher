# SPDX-License-Identifier: MPL-2.0
"""Signature-only catalogue for immediate academy UI and AI callers."""

from ..native_methods import native_target
from .native_methods import ACADEMY_SCREEN, ACADEMY


def academy_caller_targets():
    return (
        native_target(ACADEMY_SCREEN, 'academy.caller.ui', 'handle_event'),
        native_target(ACADEMY_SCREEN, 'academy.caller.draw', 'draw'),
        native_target(ACADEMY, 'academy.caller.ai_sign', 'ai_sign_drivers'),
        native_target(ACADEMY, 'academy.caller.ai_renew', 'ai_renew_contracts'),
    )
