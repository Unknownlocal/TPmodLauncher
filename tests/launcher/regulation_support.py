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
def make_mods(make_source_group):
    def create(rules, *, extra=(), report=None):
        # Direct snapshots keep dispatch cases focused; pack tests exercise the
        # public manifest reader and source loading with these same permissions.
        caps = {"regulations"} | ({"diagnostics"} if report else set())
        snapshots = []
        for owner, code in rules.items():
            manifest = SourceManifest(owner, "1.0.0", "0.4.0", "mod", frozenset(caps))
            files = (("mod.py", "import wit_world\nclass WitWorld:\n def activate(self):\n  assert not hasattr(wit_world,'bind_regulation')\n def on_event(self,n,p): raise AssertionError('must use dedicated dispatch')\n"),
                     ("rules.py", code), *extra)
            snapshots.append(SourceSnapshot(manifest, files, sum(len(text.encode()) for _, text in files)))
        mods = make_source_group(tuple(snapshots), report=report)
        for mod in mods.values():
            mod.activate()
        return mods
    return create


@pytest.fixture
def make_mod(make_mods):
    def create(rules, *, owner="owned.rules", **kwargs):
        return make_mods({owner: rules}, **kwargs)[owner]
    return create
