# SPDX-License-Identifier: MPL-2.0
"""Checked driver callers and queries that run before final signing admission."""

from ...callables import CallableTarget


# The caller bodies define refusal/commit boundaries. Query bodies define the
# private state each wrapper must supply; a signature match alone is insufficient.
_DRIVER_CALLERS = (
    ('proposal', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'accept_player_driver_proposal',
     "(self, event_id: 'str') -> 'Tuple[bool, str]'",
     'd6296ea535db85fa01796bb6a180cb68d1fc6ac5189ef8322a787a5643d7898d'),
    ('uncontested', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'sign_uncontested',
     "(self, candidate_type: 'str', candidate: 'Any', team_name: 'str', *, signer_kind: 'str' = 'team') -> 'Tuple[bool, str]'",
     '72b1d1275ef12d967e31f02ebb507c094e715ef66e26d972bcb1c6d21abb0776'),
    ('decision', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', 'resolve_player_contract_decision',
     "(self, decision_id: 'str', accept: 'bool') -> 'Tuple[bool, str]'",
     '5e43a79cbbd4c250a62f0a729ba259751e43678bfcc3766e8c0ece019ea0b22c'),
    ('offer', 'managers.personnel_negotiation_manager', 'PersonnelNegotiationManager', '_execute_offer',
     "(self, row: 'Dict[str, Any]', candidate: 'Any') -> 'bool'",
     'e77df6b8dcb9d1a9e998c52fa0589e210388409c87a0a45f6f50f6ee7c9519ef'),
    ('active', 'managers.driver', 'DriverManager', 'active_contract',
     '(self, driver: managers.driver.Driver, week: Optional[int] = None) -> Optional[Dict[str, Any]]',
     '48fd7a9b4d30a67ac080cdc5c0159b98975b161596a257e8bdab84fada75c02c'),
    ('count', 'managers.driver', 'DriverManager', 'active_driver_count',
     "(self, team_name: str, role: str = 'main', week: Optional[int] = None) -> int",
     '9e78801ec25fa651e2916d793a53ece34290a944663f8336cb5a6aef727acd72'),
    ('status', 'managers.driver', 'DriverManager', 'contract_status_at_week',
     '(self, contract: Any, week: Optional[int] = None) -> str',
     '537e94730570dd9c2b13c04d6d80a1882e20fb027eb37219e342d2c19613e4c0'),
    ('entries', 'managers.driver', 'DriverManager', 'main_contract_entries_for_week',
     '(self, team_name: str, week: int, *, exclude_driver: Optional[managers.driver.Driver] = None) -> List[Tuple[managers.driver.Driver, Dict[str, Any]]]',
     '9503acfba9bb3054fd797f0e392927754c01eb278e26619d554a102e2b49b926'),
    ('next_entries', 'managers.driver', 'DriverManager', '_next_season_main_contract_entries',
     '(self, team_name: str) -> List[Tuple[managers.driver.Driver, Dict[str, Any]]]',
     'a0a8f91647fdc2ef2af30686c2373abc99bce91556cb78a92bf73ebef9847d7c'),
    ('personalities', 'managers.driver', 'DriverManager', 'driver_personalities',
     '(self, driver: managers.driver.Driver) -> List[str]',
     'a33296a88a8feaff336709b8af875126c84f89cdb9dd7ab779f67fd612e11b40'),
    ('career', 'managers.driver', 'DriverManager', 'career_stage_modifiers',
     '(self, driver: managers.driver.Driver) -> Dict[str, Any]',
     'c99287648037d6085b7ebb14d99d99f71d2a383408a97c48daddbb1e9ea79f9d'),
    ('prestige', 'managers.prestige_manager', 'PrestigeManager', 'displayed_prestige',
     '(self, team: Dict[str, Any]) -> float',
     '1a0cdafc420b0d5df1114fc396ce8f1d7dd1ee962c867290d6afcd6f9a7ab9e0'),
)


def driver_caller_targets():
    return tuple(CallableTarget("drivers.caller." + role, module, (owner,), method, signature, (digest,))
                 for role, module, owner, method, signature, digest in _DRIVER_CALLERS)

