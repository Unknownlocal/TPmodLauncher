# SPDX-License-Identifier: MPL-2.0
"""Shared data and real source-runner fixture for regulation adapter checks."""

import pytest

from mod_runtime.source import SourceManifest, SourceSnapshot


def context():
    return {"schema": 1, "scope": "personnel.contract", "operation": "commit", "initiator": "player",
            "clock": {"season": 2026, "absolute_week": 4},
            "effective": {"season": 2026, "start_week": 4, "end_week": None}, "proposal": {}}


def selection(index=0):
    return {"rule": "native", "evaluation_season": 2026, "selected": "current",
            "current": [index, 2026], "upcoming": None}


def validator(identity="check", handler="rules.validate"):
    return {"id": identity, "rule": "native", "scope": "personnel.contract", "handler": handler}


@pytest.fixture
def make_mod(make_source_group):
    def create(rules, *, owner="owned.rules", extra=(), report=None):
        # API 0.4.0 remains unparseable by public admission. Trusted snapshots
        # exercise its private seam without publishing a capability.
        caps = {"regulations"} | ({"diagnostics"} if report else set())
        manifest = SourceManifest(owner, "1.0.0", "0.4.0", "mod", frozenset(caps))
        files = (("mod.py", "import wit_world\nclass WitWorld:\n def activate(self):\n  assert not hasattr(wit_world,'bind_regulation')\n def on_event(self,n,p): raise AssertionError('must use dedicated dispatch')\n"),
                 ("rules.py", rules), *extra)
        snapshot = SourceSnapshot(manifest, files, sum(len(text.encode()) for _, text in files))
        mod = make_source_group((snapshot,), report=report)[owner]
        mod.activate()
        return mod
    return create
