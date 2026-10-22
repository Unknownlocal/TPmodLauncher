# SPDX-License-Identifier: MPL-2.0
"""Checked shared negotiation callers and optional driver preparation queries."""

from ...callables import CallableTarget


# These paths/signatures identify shared callers and optional driver queries.
# Refusal boundaries and private-state assumptions are covered by conformance
# checks, not by runtime body hashes.
_PERSONNEL_CALLERS = (
    ('proposal', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'accept_player_driver_proposal',
     "(self, event_id: 'str') -> 'Tuple[bool, str]'"),
    ('uncontested', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'sign_uncontested',
     "(self, candidate_type: 'str', candidate: 'Any', team_name: 'str', *, signer_kind: 'str' = 'team') -> 'Tuple[bool, str]'"),
    ('decision', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'resolve_player_contract_decision',
     "(self, decision_id: 'str', accept: 'bool') -> 'Tuple[bool, str]'"),
    ('offer', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', '_execute_offer',
     "(self, row: 'Dict[str, Any]', candidate: 'Any') -> 'bool'"),
    ('due', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'resolve_due_decisions',
     "(self) -> 'None'"),
    ('ai_staff', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'ai_manage_staff',
     "(self) -> 'None'"),
    ('ai_drivers', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'ai_manage_drivers',
     "(self, role_phase: 'str' = 'all') -> 'None'"),
    ('ai_failure', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', '_fail_ai_driver_record',
     "(self, row: 'Dict[str, Any]', reason: 'str', *, permanent: 'bool' = False) -> 'None'"),
    ('active', 'managers.driver', 'DriverManager', 'active_contract',
     '(self, driver: managers.driver.Driver, week: Optional[int] = None) -> Optional[Dict[str, Any]]'),
    ('count', 'managers.driver', 'DriverManager', 'active_driver_count',
     "(self, team_name: str, role: str = 'main', week: Optional[int] = None) -> int"),
    ('status', 'managers.driver', 'DriverManager', 'contract_status_at_week',
     '(self, contract: Any, week: Optional[int] = None) -> str'),
    ('entries', 'managers.driver', 'DriverManager', 'main_contract_entries_for_week',
     '(self, team_name: str, week: int, *, exclude_driver: Optional[managers.driver.Driver] = None) -> List[Tuple[managers.driver.Driver, Dict[str, Any]]]'),
    ('next_entries', 'managers.driver', 'DriverManager', '_next_season_main_contract_entries',
     '(self, team_name: str) -> List[Tuple[managers.driver.Driver, Dict[str, Any]]]'),
    ('personalities', 'managers.driver', 'DriverManager', 'driver_personalities',
     '(self, driver: managers.driver.Driver) -> List[str]'),
    ('career', 'managers.driver', 'DriverManager', 'career_stage_modifiers',
     '(self, driver: managers.driver.Driver) -> Dict[str, Any]'),
    ('prestige', 'managers.prestige_manager', 'PrestigeManager', 'displayed_prestige',
     '(self, team: Dict[str, Any]) -> float'),
)


def personnel_caller_targets(kinds):
    # A staff-only integration needs the common negotiation/prestige methods,
    # but must not depend on driver-only modules completing or remaining compatible.
    shared = {"uncontested", "decision", "offer", "prestige", "due", "ai_staff"}
    # The driver branch includes driver queries/AI/failure classification. Its
    # final filter excludes staff AI when only driver support is compatible.
    return tuple(CallableTarget("personnel.caller." + role, module, (owner,), method, signature)
                 for role, module, owner, method, signature in _PERSONNEL_CALLERS
                 if ("driver" in kinds or role in shared) and (role != "ai_staff" or "staff" in kinds))

