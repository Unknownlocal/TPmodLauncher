# SPDX-License-Identifier: MPL-2.0
"""Signature-only catalogue for immediate academy UI and AI callers."""

from ...callables import CallableTarget


_ACADEMY_CALLERS = (
    ('ui', 'ui.screens.academy_screen', 'AcademyScreen', 'handle_event',
     '(self, event)'),
    ('draw', 'ui.screens.academy_screen', 'AcademyScreen', 'draw',
     '(self, surface)'),
    ('ai_sign', 'managers.academy_manager', 'AcademyManager', 'ai_sign_drivers',
     '(self, season: int) -> None'),
    ('ai_renew', 'managers.academy_manager', 'AcademyManager', 'ai_renew_contracts',
     '(self, current_season: int) -> None'),
)


def academy_caller_targets():
    return tuple(CallableTarget("academy.caller." + role, module, (owner,), method, signature)
                 for role, module, owner, method, signature in _ACADEMY_CALLERS)
