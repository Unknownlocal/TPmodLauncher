# SPDX-License-Identifier: MPL-2.0
"""Shared data and real source-runner fixture for regulation adapter checks."""

from contextlib import ExitStack, closing

import pytest

from launcher.source_mod import SourceMod
from mod_runtime.source import SourceManifest, SourceSnapshot


def context():
    return {"schema": 1, "scope": "personnel.contract", "operation": "commit", "initiator": "player",
            "clock": {"season": 2026, "absolute_week": 4},
            "effective": {"season": 2026, "start_week": 4, "end_week": None}, "proposal": {}}


def regulation():
    return {"schema": 1, "key": "native", "revision": "revision", "evaluation_season": 2026, "selected": "current",
            "current": {"key": "open", "effective_season": 2026, "parameters": {}, "option_data": {}}, "upcoming": None}


def validator(identity="check", handler="rules.validate"):
    return {"id": identity, "rule": "native", "scope": "personnel.contract", "handler": handler}


def call(identity="check", ctx=None, rule=None):
    return {"validator": identity, "context": context() if ctx is None else ctx,
            "regulation": regulation() if rule is None else rule}


@pytest.fixture
def make_mod(runner_bundle):
    with ExitStack() as stack:
        def create(rules, *, owner="owned.rules", extra=(), report=None):
            # API 0.4.0 remains unparseable by public admission. Trusted snapshots
            # exercise its private seam without publishing a capability.
            caps = {"regulations"} | ({"diagnostics"} if report else set())
            manifest = SourceManifest(owner, "1.0.0", "0.4.0", "mod", frozenset(caps))
            files = (("mod.py", "import wit_world\nclass WitWorld:\n def activate(self):\n  assert not hasattr(wit_world,'bind_regulation')\n def on_event(self,n,p): raise AssertionError('must use dedicated dispatch')\n"),
                     ("rules.py", rules), *extra)
            snapshot = SourceSnapshot(manifest, files, sum(len(text.encode()) for _, text in files))
            mod = stack.enter_context(closing(SourceMod(runner_bundle, snapshot, report=report)))
            mod.activate()
            return mod
        yield create
