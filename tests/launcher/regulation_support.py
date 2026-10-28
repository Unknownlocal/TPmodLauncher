# SPDX-License-Identifier: MPL-2.0
"""Shared data and real source-runner fixture for regulation adapter checks."""

import json

import pytest

from launcher.regulations import RegulationDefinitions
from launcher.regulations.shared_handler_runner import SharedHandlerRunner
from launcher.regulations.validation.dispatch import ValidationDispatch

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


def definitions(sources, *, sections=None, fallback=()):
    """Prepare the native rule and supplied owner declarations without execution."""
    data = {"categories": [{"key": "native", "options": [{"key": "open", "parameters": {}}]}],
            "mod_extensions": sections or {owner: {"schema": 1, "validators": [validator()],
                                                   "option_data": {"native": {"open": {"owner": owner}}}}
                                            for owner in sources}}
    return RegulationDefinitions.from_bytes(json.dumps(data).encode(), sources, scopes={"personnel.contract"},
                                            native_categories=fallback)


def dispatch(mods, *, count=1, sections=None):
    """Bind the supplied mods to a dispatcher; each test publishes its own policy."""
    sections = sections or {owner: {"schema": 1,
                       "validators": [validator("check" if i == 0 else f"check{i:02}") for i in range(count)],
                       "option_data": {"native": {"open": {"owner": owner}}}} for owner in mods}
    model = definitions({owner: mod.snapshot for owner, mod in mods.items()}, sections=sections)
    handler = SharedHandlerRunner.bind(next(iter(mods.values())).runner, model.validators)
    return ValidationDispatch(model, handler)


@pytest.fixture
def make_mods(make_source_group):
    def create(rules, *, extra=(), report=None, cleanup=None):
        # Direct snapshots keep dispatch cases focused; pack tests exercise the
        # public manifest reader and source loading with these same permissions.
        caps = {"regulations"} | ({"diagnostics"} if report else set())
        snapshots = []
        for owner, code in rules.items():
            manifest = SourceManifest(owner, "1.0.0", "0.4.0", "mod", frozenset(caps))
            files = (("mod.py", "import wit_world\nclass WitWorld:\n def activate(self):\n  assert not hasattr(wit_world,'bind_regulation')\n def on_event(self,n,p): raise AssertionError('must use dedicated dispatch')\n"),
                     ("rules.py", code), *extra)
            snapshots.append(SourceSnapshot(manifest, files, sum(len(text.encode()) for _, text in files)))
        mods = make_source_group(tuple(snapshots), report=report, cleanup=cleanup)
        for mod in mods.values():
            mod.activate()
        return mods
    return create


@pytest.fixture
def make_mod(make_mods):
    def create(rules, *, owner="owned.rules", **kwargs):
        return make_mods({owner: rules}, **kwargs)[owner]
    return create
