# SPDX-License-Identifier: MPL-2.0
"""Fixed native method definitions used by personnel integrations."""

from ..native_methods import define_methods


PERSONNEL = define_methods('managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', {
    '_normalize_ai_rookie_offer_duration': "(self, row: 'Dict[str, Any]', driver: 'Any' = None) -> 'None'",
    '_candidate_already_committed': "(self, candidate_type: 'str', candidate: 'Any') -> 'bool'",
    '_prestige_eligible': "(self, row: 'Dict[str, Any]', candidate: 'Any') -> 'bool'",
    'player_contract_decision_accept_block_reason': "(self, decision_id: 'str', team_name: 'Optional[str]' = None) -> 'Optional[str]'",
    'driver_contract_block_reason': "(self, team_name: 'str', driver: 'Any', role: 'str', priority: 'str', contract_start_week: 'int', *, entry_id: 'Optional[str]' = None, years: 'int' = 1, check_seat: 'bool' = True, check_prestige: 'bool' = True) -> 'Optional[str]'",
    'submit_offer': "(self, candidate_type: 'str', candidate: 'Any', team_name: 'str', offer: 'Dict[str, Any]', score: 'float', *, signer_kind: 'str' = 'team', attempts_used: 'Optional[int]' = None, annoyed: 'Optional[bool]' = None, finalize: 'bool' = False) -> 'Tuple[bool, str, Optional[Dict[str, Any]]]'",
    'accept_player_driver_proposal': "(self, event_id: 'str') -> 'Tuple[bool, str]'",
    'sign_uncontested': "(self, candidate_type: 'str', candidate: 'Any', team_name: 'str', *, signer_kind: 'str' = 'team') -> 'Tuple[bool, str]'",
    'resolve_player_contract_decision': "(self, decision_id: 'str', accept: 'bool') -> 'Tuple[bool, str]'",
    '_execute_offer': "(self, row: 'Dict[str, Any]', candidate: 'Any') -> 'bool'",
    'resolve_due_decisions': "(self) -> 'None'",
    'ai_manage_staff': "(self) -> 'None'",
    'ai_manage_drivers': "(self, role_phase: 'str' = 'all') -> 'None'",
    '_fail_ai_driver_record': "(self, row: 'Dict[str, Any]', reason: 'str', *, permanent: 'bool' = False) -> 'None'",
    '_finish_candidate_market': "(self, source_row: 'Dict[str, Any]', winner: 'Optional[Dict[str, Any]]') -> 'None'",
    '_stage_player_contract_confirmation': "(self, player_row: 'Dict[str, Any]', active_rows: 'List[Dict[str, Any]]', ranked_rows: 'List[Dict[str, Any]]') -> 'str'",
    '_notify_player': "(self, row: 'Dict[str, Any]', subject: 'str', body: 'str', *, action_label: 'Optional[str]' = None, required: 'bool' = False, marker: 'Optional[str]' = None) -> 'None'",
    '_debug_ai': "(self, row: 'Dict[str, Any]', action: 'str', details: 'Any' = '') -> 'None'",
    'begin_approach': "(self, candidate_type: 'str', candidate: 'Any', team_name: 'str', *, signer_kind: 'str' = 'team', context: 'Optional[Dict[str, Any]]' = None, initiated_by: 'str' = 'player') -> 'Tuple[bool, str, Optional[Dict[str, Any]]]'",
    '_default_target_contract_start_week': "(self, candidate_type: 'str', candidate: 'Any', team_name: 'str', *, signer_kind: 'str', context: 'Dict[str, Any]') -> 'int'",
    'get_record': "(self, candidate_type: 'str', candidate: 'Any', team_name: 'Optional[str]', *, signer_kind: 'str' = 'team') -> 'Optional[Dict[str, Any]]'",
})


PRESTIGE = define_methods('managers.prestige_manager', 'PrestigeManager', {
    'ensure_team_state': '(self, team: Dict[str, Any]) -> managers.prestige_manager.TeamPrestigeState',
    '_write_team_state': '(self, team: Dict[str, Any], state: managers.prestige_manager.TeamPrestigeState) -> None',
    'displayed_prestige': '(self, team: Dict[str, Any]) -> float',
})


EMAIL = define_methods('managers.email_manager', 'EmailManager', {
    'send_email': "(self, subject: 'str', body: 'str', week: 'Optional[int]' = None, season: 'Optional[int]' = None, category: 'Optional[str]' = None, actions: 'Optional[List[Dict]]' = None, *, team: 'Optional[str]' = None, structured_content: 'Optional[Dict]' = None)",
})
