# SPDX-License-Identifier: MPL-2.0
"""Fixed native addresses and signature selection; compatibility stays with callables."""

from types import MappingProxyType

from ..callables import CallableTarget


def define_methods(module, owner, methods):
    """Keep read-only signatures for a class, or module functions when owner is None."""
    return module, (() if owner is None else (owner,)), MappingProxyType(methods)


def native_signatures(definition, names):
    """Return a fresh signature dictionary in the order requested by an adapter."""
    _, _, methods = definition
    return {name: methods[name] for name in names.split()}


def native_target(definition, name, method):
    """Build an independent target with the adapter's hook name."""
    module, owners, methods = definition
    return CallableTarget(name, module, owners, method, methods[method])


GAME_STATE = define_methods('game_state', 'GameState', {
    'current_season_start_week': '(self)',
    'next_season_start': '(self)',
    '_team_record': '(self, name)',
    'get_hq_upkeep_pct': '(self, team: Dict[str, Any], building: str, default: float = 1.0) -> float',
    'finalize_and_rollover_season': '(self)',
})


BASE_SCREEN = define_methods('ui.base_screen', 'BaseScreen', {
    'fit_text': "(text, font, max_width, *, suffix='...')",
})
