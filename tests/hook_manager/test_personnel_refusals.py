# SPDX-License-Identifier: MPL-2.0
"""Operation-scoped refusals through the owned native caller/signing wrappers."""

from contextlib import contextmanager
import threading
from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations import regulation_personnel as implementation
from hook_manager.game_adapters.regulations.refused_proposals import RefusedProposals
from tests.hook_manager.test_personnel_adapter import owned


@contextmanager
def repeating(monkeypatch, scenario):
    state, driver, manager, planner, channel, _, _ = owned(monkeypatch)
    state.teams["other"] = {}
    state.employer, state.season, state.selection = "target", 3, "current"
    manager.row["offer"]["salary"] = 2

    def execute(self, row, candidate):
        return self.state.driver_manager.sign_driver(candidate, row["offer"]["salary"])

    def due(self):
        scenario(self)

    monkeypatch.setattr(type(manager), "_execute_offer", execute)
    monkeypatch.setattr(type(manager), "resolve_due_decisions", due)
    planner.prepare = lambda arguments: {
        "clock": {"season": state.season}, "effective": {"season": state.season},
        "proposal": {"person": {"ref": str(id(arguments["driver"]))},
                     "employer": {"ref": state.employer}, "salary": arguments["salary"],
                     "existing": arguments["driver"].contract}}
    channel.epoch, channel.closed = 1, False
    channel._generations = {"owned.rules": 1}
    channel._owners = {"owned.rules": {"revision": "first"}}
    channel._lock = threading.Lock()
    channel.actions = []
    dispatch = type(channel).__call__

    def send(self, contexts, selections, calls, deadline):
        assert not self._lock.locked()
        self.actions.append(contexts[0])
        if self.closed:
            raise RuntimeError("Owned closed channel")
        return dispatch(self, contexts, selections, calls, deadline)

    monkeypatch.setattr(type(channel), "__call__", send)
    adapter = implementation.RegulationPersonnelAdapter(state, channel,
        lambda *args: {"selected": state.selection, "current": [0, 3], "upcoming": [1, 4]}).install_prepared()
    try:
        yield state, driver, manager, channel, adapter
    finally:
        adapter.stop()


def test_unchanged_denials_share_nested_operation_but_changed_proposals_and_later_calls_recheck(monkeypatch):
    def scenario(manager):
        def attempt(person=driver):
            assert manager._execute_offer(manager.row, person) is False

        attempt()
        attempt()
        assert len(channel.actions) == 1
        # Changed and then reverted terms each need a fresh policy decision.
        manager.row["offer"]["salary"] = 3
        attempt()
        manager.row["offer"]["salary"] = 2
        attempt()
        assert len(channel.actions) == 3
        state.employer = "other"
        attempt()
        state.employer = "target"
        attempt()
        assert len(channel.actions) == 4
        attempt(SimpleNamespace(contract=None))
        assert len(channel.actions) == 5
        state.season += 1
        attempt()
        assert len(channel.actions) == 6

    with repeating(monkeypatch, scenario) as (state, driver, manager, channel, adapter):
        manager.resolve_due_decisions()
        assert not state.writes and adapter.last_outcome.status == "deny"
        assert adapter._local.personnel_caller is None and not adapter._active
        count = len(channel.actions)
        assert manager._execute_offer(manager.row, driver) is False
        assert manager._execute_offer(manager.row, driver) is False
        assert state.driver_manager.sign_driver(driver, 2) is False
        assert len(channel.actions) == count + 3


def test_policy_changes_recheck_and_cached_refusal_cannot_mask_channel_failure(monkeypatch):
    def scenario(manager):
        def attempt():
            assert manager._execute_offer(manager.row, driver) is False

        attempt()
        state.selection = "upcoming"
        attempt()
        channel.epoch += 1
        attempt()
        channel._generations["owned.rules"] += 1
        attempt()
        channel._owners["owned.rules"]["revision"] = "second"
        attempt()
        attempt()
        assert len(channel.actions) == 5
        channel.closed = True
        attempt()
        pytest.fail("Unavailable validation must unwind the native operation")

    with repeating(monkeypatch, scenario) as (state, driver, manager, channel, adapter):
        manager.resolve_due_decisions()
        assert len(channel.actions) == 6 and not state.writes
        assert adapter.last_outcome.status == "unavailable"
        assert adapter._local.personnel_caller is None and not adapter._active


def test_allowed_native_attempts_invalidate_other_refusals_even_on_false_or_exception(monkeypatch):
    def scenario(manager):
        other = SimpleNamespace(contract=None)
        # Mutate through a different person so per-person term invalidation alone
        # cannot make this test pass: a native attempt expires all prior refusals.
        for salary, fail in ((2, True), (0, False), (2, False)):
            channel.mode = "deny"
            manager.row["offer"]["salary"] = 2
            assert manager._execute_offer(manager.row, driver) is False
            before = len(channel.actions)
            channel.mode, state.native_refuse = "allow", fail
            manager.row["offer"]["salary"] = salary
            if salary == 0:
                with pytest.raises(ValueError, match="Native failure"):
                    manager._execute_offer(manager.row, other)
            else:
                assert manager._execute_offer(manager.row, other) is (not fail)
            channel.mode = "deny"
            manager.row["offer"]["salary"] = 2
            assert manager._execute_offer(manager.row, driver) is False
            assert len(channel.actions) == before + 2

    with repeating(monkeypatch, scenario) as (state, driver, manager, channel, adapter):
        manager.resolve_due_decisions()
        assert state.writes == [2, 0, 2] and driver.contract is None
        assert adapter.last_outcome.status == "deny" and set(adapter.owners.values()) == {"ready"}
        assert adapter._local.personnel_caller is None and not adapter._active


def test_cached_comparison_excludes_concurrent_policy_changes(monkeypatch):
    def scenario(manager):
        assert manager._execute_offer(manager.row, driver) is False
        comparing, attempted = threading.Event(), threading.Event()
        changed = []

        def publish():
            if comparing.wait(2):
                acquired = channel._lock.acquire(blocking=False)
                try:
                    if acquired:
                        channel._generations["owned.rules"] += 1
                        channel.closed = True
                        changed.append(True)
                finally:
                    if acquired:
                        channel._lock.release()
            attempted.set()

        original = RefusedProposals._value

        def compare(value):
            # Attempt publication during comparison to check that the lock protects
            # the entire cached-refusal decision.
            if type(value) is dict and value.get("scope") == "personnel.contract" and not comparing.is_set():
                comparing.set()
                assert attempted.wait(2) and not changed
            return original(value)

        monkeypatch.setattr(RefusedProposals, "_value", staticmethod(compare))
        thread = threading.Thread(target=publish)
        thread.start()
        try:
            assert manager._execute_offer(manager.row, driver) is False
        finally:
            thread.join(2)
        assert not thread.is_alive() and len(channel.actions) == 1
        with channel._lock:
            channel._generations["owned.rules"] += 1
        assert manager._execute_offer(manager.row, driver) is False
        assert len(channel.actions) == 2

    with repeating(monkeypatch, scenario) as (state, driver, manager, channel, adapter):
        adapter.seconds = 2
        manager.resolve_due_decisions()
        assert not state.writes and adapter.last_outcome.status == "deny"
