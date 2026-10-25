# SPDX-License-Identifier: MPL-2.0
"""Exercise real shared imports and entry state without pack/provider cutover."""

import json
from types import SimpleNamespace

import pytest

from launcher.shared_runner_host import bind_shared_operations
from launcher.sandbox import Sandbox, SandboxError
from mod_runtime.bundle import EXPORTS
from mod_runtime.source_transfer import RUNNER_LIMITS, SOURCE_FUEL
from mod_runtime import shared_sources
from mod_runtime.shared_sources import SharedSources
from mod_runtime.source import SourceManifest, SourceSnapshot


ENTRY = '''import json
import wit_world
import helpers.child
import helpers.child as child
from rules import VALUE, SHARED
from pkg import PACKAGE
from importlib import import_module
class WitWorld:
 def __init__(self): self.count = 0
 def activate(self): self.count += 1
 def on_event(self, name, payload):
  if name == 'touch': self.count += 1
  if name == 'peer':
   peer = wit_world.import_mod(json.loads(payload), 'rules')
   assert peer is import_module(peer.__name__)
   peer.SHARED.append(VALUE)
  return json.dumps([VALUE, helpers.child.VALUE, child.VALUE, PACKAGE,
                     import_module('.rules', wit_world.import_mod(OWNER, 'rules').__package__).VALUE,
                     self.count, SHARED])
'''


def snapshot(owner, value, *, entry="mod", code=ENTRY):
    files = {
        entry.replace(".", "/") + ".py": f"OWNER = {owner!r}\n" + code,
        "rules.py": f"VALUE = {value!r}\nSHARED = []\n",
        "helpers/__init__.py": "",
        "helpers/child.py": "from rules import VALUE\n",
        "pkg/__init__.py": "from .helper import VALUE as PACKAGE\n",
        "pkg/helper.py": "from rules import VALUE\n",
    }
    if "." in entry:
        files[entry.split(".")[0] + "/__init__.py"] = ""
    manifest = SourceManifest(owner, "1.0.0", "0.3.0", entry, frozenset())
    return SourceSnapshot(manifest, tuple(files.items()), sum(len(text.encode()) for text in files.values()))


def sandbox(bundle, cleaned):
    return Sandbox(bundle.component(), bind_shared_operations({}, {}), limits=RUNNER_LIMITS,
                   required_exports=dict(EXPORTS), cleanup=lambda: cleaned.append(True))


def test_shared_imports_preserve_owner_state_and_allow_cross_mod_objects(runner_bundle):
    first = snapshot("owned.alpha", "alpha", code=(
        "import wit_world\nPEER = wit_world.import_mod('owned.beta', 'rules')\n" + ENTRY))
    second = snapshot("owned.beta", "beta", entry="entry.main")
    cleaned = []
    with sandbox(runner_bundle, cleaned) as guest:
        SharedSources(guest, (first, second))
        for owner in ("owned.alpha", "owned.beta"):
            guest.call("activate-source", owner)
        # Identical local import names resolve within the requesting mod. The
        # first entry also imported beta before beta's entry object was loaded.
        assert json.loads(guest.call("on-source-event", "owned.alpha", "touch", "{}")) == ["alpha"] * 5 + [2, []]
        assert json.loads(guest.call("on-source-event", "owned.beta", "read", "{}")) == ["beta"] * 5 + [1, []]
        guest.call("on-source-event", "owned.alpha", "peer", '"owned.beta"')
        assert json.loads(guest.call("on-source-event", "owned.beta", "read", "{}"))[-1] == ["alpha"]
        assert not guest.closed
    assert cleaned == [True]


def test_later_entry_failure_closes_the_whole_shared_runner(runner_bundle):
    cleaned = []
    with sandbox(runner_bundle, cleaned) as guest:
        with pytest.raises(SandboxError, match="second entry failed"):
            SharedSources(guest, (snapshot("owned.alpha", "alpha"),
                                  snapshot("owned.beta", "beta", code="raise ValueError('second entry failed')")))
        assert guest.closed and cleaned == [True]
    assert cleaned == [True]


@pytest.mark.parametrize("failure", ["duplicate-owner", "late-transfer"])
def test_guest_rejects_invalid_shared_transfer_order(runner_bundle, failure):
    cleaned = []
    with sandbox(runner_bundle, cleaned) as guest:
        guest.call("declare-source", "owned.alpha", "mod")
        guest.call("put-owner-source", "owned.alpha", "mod.py", "class WitWorld: pass")
        with pytest.raises(SandboxError):
            if failure == "duplicate-owner":
                guest.call("declare-source", "owned.alpha", "mod")
            else:
                guest.call("seal-sources")
                guest.call("put-owner-source", "owned.alpha", "mod.py", "# too late")
        assert guest.closed and cleaned == [True]


def test_group_source_budget_is_checked_before_guest_exports(monkeypatch):
    first, second = snapshot("owned.alpha", "a"), snapshot("owned.beta", "b")
    monkeypatch.setattr(shared_sources, "MAX_GROUP_SOURCE_BYTES", first.total_bytes + second.total_bytes - 1)
    calls = []
    guest = SimpleNamespace(close=lambda: calls.append("closed"), call=lambda *args: calls.append(args))
    with pytest.raises(SandboxError, match="group exceeds its budget"):
        SharedSources(guest, (first, second))
    assert calls == ["closed"]


def test_guest_bounds_owner_count_independently_of_each_mod(runner_bundle):
    from mod_runtime._source_policy import MAX_SOURCE_OWNERS
    cleaned = []
    with sandbox(runner_bundle, cleaned) as guest:
        with pytest.raises(SandboxError):
            with guest.phase(fuel=SOURCE_FUEL, seconds=5):
                for index in range(MAX_SOURCE_OWNERS + 1):
                    guest.call("declare-source", f"owned.m{index}", "mod")
        assert guest.closed and cleaned == [True]
