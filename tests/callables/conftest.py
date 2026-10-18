# SPDX-License-Identifier: MPL-2.0
"""Own synthetic modules and guard engines for private callable checks."""

import sys
from types import ModuleType

import pytest

from hook_manager.callables import CallableAdapters


@pytest.fixture
def host(monkeypatch):
    module = ModuleType("owned_private_calls")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    engines = []

    def create(dispatch=None, **kwargs):
        engine = CallableAdapters(dispatch or (
            lambda owner, calls, deadline: [{"decision": "allow"} for call in calls]), **kwargs)
        engines.append(engine)
        return module, engine

    yield create
    for engine in engines:
        engine.stop()
