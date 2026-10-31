# SPDX-License-Identifier: MPL-2.0
"""Own synthetic modules and guard engines for private callable checks."""

import sys
from collections import defaultdict
from types import ModuleType

import pytest

from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.framing import regulation_decisions
from hook_manager.manager import HookError


@pytest.fixture
def host(monkeypatch):
    module = ModuleType("owned_private_calls")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    engines = []

    def create(dispatch=None):
        # Exercise the production shared guard. Only the external channel is a
        # stand-in; these tests own native wrapper lifecycle and worker access.
        # The channel supplies complete checked replies before guards use them.
        def channel(contexts, selections, calls, deadline):
            result = channel.dispatch(contexts, selections, calls, deadline)
            if type(result) is not dict or result.keys() != calls.keys():
                raise HookError("Incomplete regulation owners")
            return {owner: regulation_decisions(result[owner], len(rows)) for owner, rows in calls.items()}
        channel.dispatch = dispatch or (lambda contexts, selections, calls, deadline:
            {owner: [{"decision": "allow"} for _ in rows] for owner, rows in calls.items()})
        channel.bindings = lambda scope: defaultdict(lambda: [(0, "native")])
        channel._unavailable = set()
        engine = RegulationGuards(channel, lambda rule, season: {
            "selected": "current", "current": [0, season], "upcoming": None}, "personnel.contract")
        engines.append(engine)
        return module, engine

    yield create
    for engine in engines:
        engine.stop()
