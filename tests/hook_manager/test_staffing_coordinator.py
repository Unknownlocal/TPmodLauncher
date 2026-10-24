# SPDX-License-Identifier: MPL-2.0
"""Required staffing stays private until validation, completion and freshness agree."""

from copy import deepcopy
import inspect
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallBinding, CallableTarget, ResultContract
from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.personnel_adapter import PersonnelAdapter
from hook_manager.game_adapters.regulations import personnel_adapter as implementation
from hook_manager.game_adapters.regulations.staffing_coordinator import StaffingCoordinator
from hook_manager.game_adapters.staff import staffing
from hook_manager.game_adapters.staff.staffing import StaffingPlanner
from hook_manager.manager import HookError
from tests.hook_manager.test_staffing_planner import owned


def coordinator(monkeypatch, mode="allow", *, callers=False):
    state, manager, person = owned(monkeypatch)
    planner = StaffingPlanner(state)

    class Channel:
        def __init__(self):
            self.epoch, self.closed, self.mode = 1, False, mode
            self._lock = threading.Lock()
            self._generations = dict.fromkeys(("owned.rules", "peer.rules"), 1)
            self._owners = {owner: {"revision": "owned"} for owner in self._generations}
            self._unavailable, self.actions, self.calls = set(), [], []
            self.after = lambda: None

        def bindings(self, scope):
            return {owner: ((0, "rule"),) for owner in self._owners}

        def __call__(self, contexts, selections, calls, deadline):
            assert contexts[0]["initiator"] in ("system", "unknown") and contexts[0]["operation"] == "commit"
            self.actions.append(contexts[0])
            self.calls.append(calls)
            if self.mode == "unavailable":
                self._unavailable.add("owned.rules")
                raise RuntimeError("Owned unavailable handler")
            result = {owner: [{"decision": "allow"} if self.mode == "allow" else
                              {"decision": "deny", "message": "Owned policy refusal"}] for owner in calls}
            self.after()
            return result

    class Guards(PersonnelAdapter):
        def __init__(self, channel):
            RegulationGuards.__init__(self, channel, lambda *args: {"selected": "current"}, "personnel.contract")
            self.planners, self.feedback = {"staff": planner.contracts}, None
            self._personnel_operation = threading.Lock()

    channel = Channel()
    module = ModuleType("owned_staffing_coordinator")
    module.StaffManager = type(manager)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    signing = CallableTarget("staff.sign", module.__name__, ("StaffManager",), "sign_staff", str(inspect.signature(planner.sign)))
    if callers:
        targets = tuple(CallableTarget("staffing." + role, module.__name__, ("StaffManager",), method,
                                      str(inspect.signature(getattr(type(manager), method))))
                        for role, method in (("onboard", "onboard_joining_team_staff"), ("recover", "ensure_active_teams_have_staff")))
        def unavailable(state):
            raise HookError("Owned fixture has no driver manager")
        monkeypatch.setattr(implementation, "DriverContractPlanner", unavailable)
        monkeypatch.setattr(implementation, "StaffContractPlanner", lambda state: planner.contracts)
        monkeypatch.setattr(implementation, "StaffingPlanner", lambda state: planner)
        monkeypatch.setattr(implementation, "staff_target", lambda: signing)
        monkeypatch.setattr(implementation, "staffing_targets", lambda: targets)
        monkeypatch.setattr(implementation, "personnel_caller_targets", lambda kinds: ())
        monkeypatch.setattr(implementation, "check_preparation", lambda *args: None)
        reports = []
        guards = PersonnelAdapter.create(state, channel, lambda *args: {"selected": "current"}, reports.append)
        assert guards is not None and guards.staffing is not None
        assert len(reports) == 1 and "no driver manager" in reports[0]
        return state, manager, person, channel, guards, guards.staffing
    guards = Guards(channel)
    guards.approve(signing)
    codec = ResultContract({"shape": "bool", "denied": {"literal": False}})
    project = guards._projection
    for owner in channel._owners:
        guards.register(owner, [CallBinding("staff", "staff.sign", project, codec)])
    return state, manager, person, channel, guards, StaffingCoordinator(guards, planner)


def test_native_staffing_callers_preserve_returns_and_discard_refused_batch(monkeypatch):
    state, manager, person, channel, guards, run = coordinator(monkeypatch, callers=True)
    before = deepcopy((vars(person), manager.staff_season_history))
    channel.after = lambda: setattr(channel, "mode", "deny")
    try:
        # A later refusal must discard the earlier private signing too. Native
        # recovery returns None regardless; this is not permission to publish.
        assert manager.ensure_active_teams_have_staff() is None
        assert manager.staff == [person] and (vars(person), manager.staff_season_history) == before
        assert guards.last_outcome.status == "deny" and guards.errors[-1] == "Owned policy refusal"
        channel.mode, channel.after = "allow", lambda: None
        assert manager.onboard_joining_team_staff("first", contract_years=2) == [person.name]
        assert manager.ensure_active_teams_have_staff() is None and len(manager.staff) == 2
        assert person.contract == {"team": "first"} and manager.staff[1].contract == {"team": "second"}
        other = type(manager)()
        other.state = SimpleNamespace()
        # A cached wrapper for another manager keeps its original call path.
        monkeypatch.setattr(other, "team_has_active_staff", lambda *a, **kw: True)
        assert other.onboard_joining_team_staff("other") == []
    finally:
        guards.stop()
    assert not hasattr(type(manager).onboard_joining_team_staff, "_tpm_hook_group")


def test_pending_staffing_callers_do_not_prepare_or_fail_healthy_owners(monkeypatch):
    _, manager, person, channel, guards, run = coordinator(monkeypatch, callers=True)
    channel.validation_ready = False
    monkeypatch.setattr(run.planner, "prepare_onboarding", lambda *a, **kw: pytest.fail("Pending staffing copied inputs"))
    try:
        assert manager.onboard_joining_team_staff("first") == [] and person.contract is None
        assert not channel.actions and set(guards.owners.values()) == {"ready"}
        channel.validation_ready = True
        prepare = run.planner.prepare_recovery
        def close_after_preparing(*args, **kwargs):
            plan = prepare(*args, **kwargs)
            channel.validation_ready = False
            return plan
        monkeypatch.setattr(run.planner, "prepare_recovery", close_after_preparing)
        assert manager.ensure_active_teams_have_staff() is None
        assert manager.staff == [person] and person.contract is None and not person.morale_effects
        assert set(guards.owners.values()) == {"ready"}
    finally:
        guards.stop()


@pytest.mark.parametrize("enrolled", [False, True])
def test_native_staffing_worker_grant_lasts_only_for_the_call(monkeypatch, enrolled):
    _, manager, person, channel, guards, run = coordinator(monkeypatch, callers=True)
    entered, release = threading.Event(), threading.Event()
    results, errors = [], []
    def wait():
        entered.set()
        assert release.wait(2)
    channel.after = wait
    def worker():
        try:
            results.append(manager.ensure_active_teams_have_staff())
            results.append((threading.current_thread() in guards._threads, guards._local.operation))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=worker)
    if enrolled:
        guards.enroll(thread)
    try:
        thread.start()
        assert entered.wait(2)
        assert not guards.try_stop() and thread in guards._threads
        release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors and results == [None, (enrolled, False)]
        assert person.contract == {"team": "first"} and len(manager.staff) == 2 and guards._active == 0
    finally:
        release.set()
        thread.join(3)
        guards.stop()


def test_complete_staffing_publishes_native_effects_and_preserves_canonical_owners(monkeypatch):
    state, manager, person, channel, guards, run = coordinator(monkeypatch)
    roster, history, effects = manager.staff, manager.staff_season_history, person.morale_effects
    old_history = history[person.name]
    try:
        result = run.recover()
        assert result.published and result.native_result is None and not result.missing and result.refusal is None
        assert manager.staff is roster and manager.staff_season_history is history
        assert roster[0] is person and person.contract == {"team": "first"}
        assert person.morale_effects is effects and effects == [{"amount": 1}]
        assert history[person.name] is old_history and history["Generated"] == []
        generated = roster[1]
        assert generated.contract == {"team": "second"}
        assert channel.actions[1]["proposal"]["person"]["ref"] == "staff:" + str(id(generated))
        assert not guards._active and guards._local.staffing_caller is None
    finally:
        guards.stop()


def test_denied_unavailable_and_native_incomplete_staffing_discard_all_private_work(monkeypatch):
    for mode in ("deny", "unavailable", "late_deny", "native_false"):
        state, manager, person, channel, guards, run = coordinator(monkeypatch, mode if mode in ("deny", "unavailable") else "allow")
        before = deepcopy((vars(person), manager.staff_season_history))
        if mode == "late_deny":
            channel.after = lambda: setattr(channel, "mode", "deny")
        manager.refuse = mode == "native_false"
        try:
            result = run.recover()
            assert not result.published and manager.staff == [person]
            assert (vars(person), manager.staff_season_history) == before
            if mode == "native_false":
                assert result.refusal is None and guards.last_outcome.status == "allow"
            else:
                assert result.refusal.status == ("unavailable" if mode == "unavailable" else "deny")
            assert bool(result.missing) is (mode != "unavailable")
            assert len(channel.actions) == (1 if mode == "unavailable" else 2)
            assert not guards._active and guards._local.staffing_caller is None
        finally:
            guards.stop()


def test_stale_inputs_and_policy_block_publication_without_overwriting_external_changes(monkeypatch):
    class Contract(dict):
        pass

    for change in ("roster", "clock", "policy", "contract", "copy"):
        state, manager, person, channel, guards, run = coordinator(monkeypatch)
        extra = SimpleNamespace(name="External", role="chief_designer", retired=True, contract=None)
        if change == "contract":
            person.contract = Contract()
        if change == "copy":
            # Change the live roster after its private list was copied but before
            # planning. Dispatch-time mutations do not exercise this early gap.
            original_copy = staffing.copy
            def copy_with_external_publication(value):
                private = original_copy(value)
                if value is manager._name_pool:
                    extra.retired, extra.contract = False, {"team": "first"}
                    manager.staff.append(extra)
                return private
            monkeypatch.setattr(staffing, "copy", copy_with_external_publication)
        def mutate():
            if change == "roster" and all(member is not extra for member in manager.staff):
                manager.staff.append(extra)
            elif change == "clock":
                state.week_index += 1
            elif change == "policy":
                channel._generations["owned.rules"] += 1
            elif change == "contract":
                person.contract["team"] = "external"
        channel.after = mutate
        try:
            result = run.recover()
            assert not result.published and result.refusal.status == "unavailable"
            assert person.contract == ({"team": "external"} if change == "contract" else None)
            assert not person.morale_effects
            assert manager.staff == ([person, extra] if change in ("roster", "copy") else [person])
            assert "Generated" not in manager.staff_season_history and not guards._active
        finally:
            guards.stop()


def test_removed_owners_do_not_validate_or_block_later_staffing(monkeypatch):
    state, manager, person, channel, guards, run = coordinator(monkeypatch)
    try:
        guards.remove_owner("owned.rules")
        assert run.onboard("first").published
        assert set(channel.calls[-1]) == {"peer.rules"}
        guards.remove_owner("peer.rules")
        actions = len(channel.actions)
        channel.closed = True
        channel._lock.acquire()
        try:
            assert run.onboard("second").published
        finally:
            channel._lock.release()
        assert len(channel.actions) == actions and manager.staff[-1].contract == {"team": "second"}
    finally:
        guards.stop()


def test_generation_error_and_unadmitted_worker_leave_staffing_private(monkeypatch):
    state, manager, person, channel, guards, run = coordinator(monkeypatch)
    create = run.planner.methods["staff"]["_create_free_staff_for_role"][0]
    def failed(self, role, rng):
        create(self, role, rng)
        raise ValueError("Owned generation error")
    run.planner.methods["staff"]["_create_free_staff_for_role"] = failed, False
    try:
        with pytest.raises(ValueError, match="Owned generation error"):
            run.recover()
        assert manager.staff == [person] and person.contract is None and not guards._active
        errors = []
        def worker():
            try:
                run.recover()
            except HookError as error:
                errors.append(str(error))
        for enrolled in (False, True):
            thread = threading.Thread(target=worker)
            if enrolled:
                guards.enroll(thread)
            thread.start()
            thread.join(2)
            assert not thread.is_alive()
        assert len(errors) == 2
        assert manager.staff == [person] and person.contract is None and not guards._active
    finally:
        guards.stop()


def test_admitted_worker_outcomes_freeze_configuration_and_clean_up(monkeypatch):
    for mode in ("allow", "deny", "unavailable", "nested", "removed"):
        state, manager, person, channel, guards, run = coordinator(monkeypatch, mode if mode in ("deny", "unavailable") else "allow")
        entered, proceed = threading.Event(), threading.Event()
        results, errors = [], []
        before = deepcopy((vars(person), manager.staff_season_history))
        if mode == "nested":
            channel.after = lambda: run.onboard("first")
        if mode == "removed":
            for owner in tuple(guards.owners):
                guards.remove_owner(owner)
        def worker():
            try:
                with guards.operation():
                    entered.set()
                    assert proceed.wait(2)
                    result = run.recover()
                    results.append((result, guards.last_outcome, getattr(guards._local, "staffing_caller", None), guards._active))
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        guards.enroll(thread)
        try:
            thread.start()
            assert entered.wait(2)
            with pytest.raises(HookError, match="during an operation"):
                guards.remove_owner("owned.rules")
            assert not guards.try_stop() and not guards.closed
            proceed.set()
            thread.join(2)
            assert not thread.is_alive() and not errors and len(results) == 1
            result, outcome, caller, active = results[0]
            assert caller is None and active == 1 and not guards._active
            assert guards.last_outcome is None
            assert result.published is (mode == "allow")
            if mode == "allow":
                assert outcome.status == "allow" and len(manager.staff) == 2
                assert person.contract == {"team": "first"}
            else:
                assert result.refusal.status == outcome.status == ("deny" if mode == "deny" else "unavailable")
                assert manager.staff == [person] and (vars(person), manager.staff_season_history) == before
                if mode == "removed":
                    assert not channel.actions and not guards._personnel_operation.locked()
        finally:
            proceed.set()
            thread.join(2)
            guards.stop()


def test_busy_personnel_operations_cannot_overlap_and_worker_can_retry(monkeypatch):
    for operation in ("signing", "staffing"):
        state, manager, person, channel, guards, run = coordinator(monkeypatch)
        rival = StaffingCoordinator(guards, run.planner)
        entered, release, first_done = threading.Event(), threading.Event(), threading.Event()
        values, errors = {}, []
        before = deepcopy((vars(person), manager.staff_season_history))
        if operation == "signing":
            execute = guards._execute
            def paused(original, args, kwargs):
                # Validation has allowed, but native writes have not run yet.
                entered.set()
                assert release.wait(2)
                return execute(original, args, kwargs)
            monkeypatch.setattr(guards, "_execute", paused)
            first_call = lambda: manager.sign_staff("second", person, 2., 1, bypass_requirements=True)
        else:
            prepare = run.planner.prepare_onboarding
            def paused(*args, **kwargs):
                plan = prepare(*args, **kwargs)
                # A complete private plan is waiting to publish. Another worker
                # must not begin its own snapshot against changing dictionaries.
                entered.set()
                assert release.wait(2)
                return plan
            monkeypatch.setattr(run.planner, "prepare_onboarding", paused)
            first_call = lambda: run.onboard("second")

        def first_worker():
            try:
                with guards.operation():
                    values["first"] = first_call()
            except BaseException as error:
                errors.append(error)
            finally:
                first_done.set()

        def second_worker():
            try:
                with guards.operation():
                    values["busy"] = rival.onboard("first")
                    if operation == "signing":
                        assert manager.sign_staff("first", person, 2., 1, bypass_requirements=True) is False
                        assert guards.last_outcome.status == "unavailable"
                    assert set(guards.owners.values()) <= {"ready"}
                    assert manager.staff == [person] and (vars(person), manager.staff_season_history) == before
                    # Contention did not poison the validators. Once the first
                    # run ends, the same worker can prepare a fresh legal action.
                    release.set()
                    assert first_done.wait(2)
                    values["retry"] = rival.onboard("first")
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=call) for call in (first_worker, second_worker)]
        for thread in threads:
            guards.enroll(thread)
        try:
            threads[0].start()
            assert entered.wait(2)
            threads[1].start()
            for thread in threads:
                thread.join(3)
            assert all(not thread.is_alive() for thread in threads) and not errors, errors
            assert values["first"] is True if operation == "signing" else values["first"].published
            assert not values["busy"].published and values["busy"].refusal.status == "unavailable"
            assert values["retry"].published and not guards._active
            assert len(manager.staff) == 2 and person.contract == {"team": "second"}
            assert person.morale_effects == [{"amount": 1}] and manager.staff[1].contract == {"team": "first"}
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(2)
            guards.stop()
