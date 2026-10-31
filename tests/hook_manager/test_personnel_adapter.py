# SPDX-License-Identifier: MPL-2.0
"""Driver integration lifecycle and caller cancellation on portable owned code."""

import inspect
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from hook_manager.manager import HookError
from hook_manager.game_adapters.regulations import regulation_personnel as implementation
from hook_manager.game_adapters.personnel.market_preparation import MarketPreparation
from tests.hook_manager.negotiation_support import negotiation_state as owned, submitted_negotiation as submitted


def test_rollover_worker_retains_candidate_refusals_and_releases_admission(monkeypatch):
    state, denied, _, _, channel, _, _ = owned(monkeypatch)
    accepted = SimpleNamespace(contract=None)
    state.rollover_candidates.append(accepted)
    dispatch = type(channel).__call__
    modes = iter(("deny", "allow"))

    def decide(*args):
        channel.mode = next(modes)
        return dispatch(*args)

    monkeypatch.setattr(type(channel), "__call__", decide)
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {}).install_prepared()
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            worker.submit(state.finalize_and_rollover_season).result(2)
            assert state.rollover_complete and state.rollover_signed is accepted
            assert denied.contract is None and accepted.contract == {"salary": 2}
            assert [c["initiator"] for c in channel.contexts] == ["system", "system"]
            # Reusing the thread outside the mapped entry point must not retain
            # permission to sign another contract.
            assert worker.submit(state.driver_manager.sign_driver, denied).result(2) is False
        assert denied.contract is None and not adapter._active
        assert state.writes == [2]
    finally:
        adapter.stop()


@pytest.mark.parametrize("swallow", [False, True])
def test_rollover_unavailable_stops_retries_and_survives_native_catch(monkeypatch, swallow):
    state, person, _, _, channel, _, _ = owned(monkeypatch)
    channel.mode = "failed"

    def rollover(self):
        try:
            self.driver_manager.sign_driver(person)
        except Exception:
            if not swallow:
                raise
        finally:
            self.native_cleanup = True
        self.native_returned = True

    monkeypatch.setattr(type(state), "finalize_and_rollover_season", rollover)
    reports = []
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {}, submit_log=reports.append).install_prepared()
    try:
        with ThreadPoolExecutor(max_workers=1) as worker:
            with pytest.raises(HookError, match="Season rollover stopped"):
                worker.submit(state.finalize_and_rollover_season).result(2)
            assert worker.submit(implementation.native_initiator).result(2) == "unknown"
        assert state.native_cleanup and getattr(state, "native_returned", False) is swallow
        assert person.contract is None and not state.writes and len(channel.contexts) == 1
        assert not adapter._active and not adapter._personnel_operation.locked()
        assert len(reports) == 1 and reports[0]["level"] == "ERROR"
        assert "Owned handler failed" in reports[0]["detail"]
    finally:
        adapter.stop()


def test_submission_refusal_preserves_talks_and_final_offer_then_rechecks_signing(monkeypatch):
    state, person, negotiations, channel, adapter = submitted(monkeypatch)
    offer = {"salary_m": 2.5, "years": 4}
    before = dict(negotiations.row)
    try:
        monkeypatch.setattr(adapter.feedback, "refuse", lambda *a: pytest.fail("Submission opened signing dialog"))
        assert negotiations.submit_offer(" Driver ", person, "target", offer, 100, finalize=True) == (
            False, "Owned refusal", negotiations.row)
        assert negotiations.row == before and not negotiations.candidate_deadlines and not state.writes
        assert channel.contexts[-1]["operation"] == "preview"
        assert channel.contexts[-1]["proposal"]["proposed"] == {"salary_m": 2.5, "duration_weeks": 208}
        assert negotiations.submit_offer("driver", person, "target", {"salary_m": "invalid"}, 100) == (
            False, "Invalid contract offer terms.", negotiations.row)
        assert adapter.owners == {"owned.rules": "ready"} and negotiations.row == before
        # A prior approach/allowance is not reused: a changed rule denies even
        # an already submitted final offer without replacing its stored terms.
        channel.mode = "allow"
        assert negotiations.submit_offer("driver", person, "target", offer, 100, finalize=True)[0]
        stored = negotiations.row["offer"]
        channel.mode = "deny"
        assert not negotiations.submit_offer("driver", person, "target", {"years": 1}, 100)[0]
        assert negotiations.row["offer"] is stored and stored == offer
        assert not negotiations._execute_offer(negotiations.row, person)
        assert person.contract is None  # Submission allowance never authorises signing.
    finally:
        adapter.stop()


def test_submission_checks_and_detaches_planner_data_before_dispatch(monkeypatch):
    state, person, negotiations, channel, adapter = submitted(monkeypatch)
    planner = adapter.planners["driver"]
    prepared = planner._build_proposal({"salary_m": 2.5, "years": 4})
    planner._build_proposal = lambda arguments: prepared
    before = dict(negotiations.row)
    try:
        assert not negotiations.submit_offer("driver", person, "target", {"years": 4}, 100)[0]
        channel.contexts[-1]["proposal"]["proposed"]["salary_m"] = 99
        assert prepared["proposal"]["proposed"]["salary_m"] == 2.5
        prepared["proposal"]["person"] = {"name": "x" * 4097}
        result = negotiations.submit_offer("driver", person, "target", {"years": 4}, 100)
        assert result == (False, "Validation unavailable", negotiations.row)
        assert len(channel.contexts) == 1 and negotiations.row == before
        assert not state.writes and person.contract is None
    finally:
        adapter.stop()


def test_worker_submission_unavailability_preserves_pending_offer(monkeypatch):
    state, person, negotiations, channel, adapter = submitted(monkeypatch)
    preparing, release = threading.Event(), threading.Event()
    arguments = adapter.offers.arguments
    def held_arguments(*args):
        preparing.set()
        assert release.wait(3)
        return arguments(*args)
    monkeypatch.setattr(adapter.offers, "arguments", held_arguments)
    try:
        channel.mode = "failed"
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(negotiations.submit_offer, "driver", person, "target", {"years": 2}, 100)
            try:
                assert preparing.wait(3)
                with pytest.raises(HookError, match="during an operation"):
                    adapter.stop()
            finally:
                release.set()
            result = pending.result()
        assert result == (False, "Validation unavailable", negotiations.row)
        assert negotiations.row["status"] == "talks_open" and "offer_score" not in negotiations.row
        assert not negotiations.candidate_deadlines and not state.writes and person.contract is None
    finally:
        adapter.stop()


def test_nested_offer_reuses_then_discards_query_inputs_without_reaching_signing(monkeypatch):
    state, driver, negotiations, planner, channel, _, _ = owned(monkeypatch)
    state.driver_manager.normalize_contract_status = None
    observed, stores = [], []

    def active(self, driver, week=None):
        observed.append(driver)
        return None

    def read():
        frame = adapter._local.personnel_caller
        stores.append(frame["query_inputs"])
        bound = inspect.signature(active).bind(state.driver_manager, driver, 10)
        adapter.callers.queries.run("active", active, bound, frame)

    def offer(self, row, candidate):
        read()
        return False  # Native eligibility can stop an offer before any mod check.

    def decision(self, decision_id, accept):
        read()
        assert self._execute_offer(self.row, self.driver) is False
        assert not stores[-1].drivers
        read()
        raise ValueError("Native confirmation failed")

    monkeypatch.setattr(type(negotiations), "_execute_offer", offer)
    monkeypatch.setattr(type(negotiations), "resolve_player_contract_decision", decision)
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    try:
        with pytest.raises(ValueError, match="Native confirmation failed"):
            negotiations.resolve_player_contract_decision("decision", True)
        assert stores[0] is stores[1] is stores[2]
        assert observed[0] is observed[1] and observed[1] is not observed[2]
        assert not stores[0].drivers and not stores[0].managers
        assert adapter._local.personnel_caller is None and not state.writes
        assert "decision" in negotiations.pending and driver.contract is None
    finally:
        adapter.stop()


def test_market_checkpoints_keep_prior_candidate_mail_and_discard_blocked_preparation(monkeypatch):
    state, driver, negotiations, planner, channel, _, _ = owned(monkeypatch)
    state.email_manager = sys.modules["owned_personnel_adapter"].EmailManager(state)
    first, blocked = negotiations.row, {"candidate_type": "driver", "offer": {"years": 3}, "candidate_key": "b"}
    first["candidate_key"] = "a"
    first["context"] = {"planned_offer": first["offer"]}
    negotiations.records = {"first": first, "blocked": blocked}
    negotiations.candidate_deadlines = {"a": 114, "b": 114}
    other = SimpleNamespace(contract=None)
    negotiations.markets = [(["first"], driver), (["blocked"], other)]
    roots = negotiations.records, negotiations.candidate_deadlines, state.email_manager.inbox, first["offer"]
    original = type(channel).__call__
    modes = iter(("allow", "failed"))

    def dispatch(*args):
        channel.mode = next(modes)
        return original(*args)

    monkeypatch.setattr(type(channel), "__call__", dispatch)
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    try:
        negotiations.resolve_due_decisions()
        assert first["status"] == "completed" and driver.contract == {"salary": 2} and other.contract is None
        assert negotiations.candidate_deadlines == {"b": 114} and "prepared" not in blocked
        assert blocked["offer"]["years"] == 3 and blocked["execution_block_reason"] == "Validation unavailable"
        assert [mail["subject"] for mail in state.email_manager.inbox] == ["Market completed", "Native signed", "Preparing first"]
        assert state.email_manager.urgent_sound_serial == 3
        assert roots[0] is negotiations.records and roots[1] is negotiations.candidate_deadlines
        assert roots[2] is state.email_manager.inbox and roots[3] is first["offer"] is first["context"]["planned_offer"]
    finally:
        adapter.stop()


def test_market_save_publishes_aliases_and_new_containers_without_exposing_private_writes(monkeypatch):
    state, _, negotiations, _, _, _, _ = owned(monkeypatch)
    offer = negotiations.row["offer"]
    negotiations.row["context"] = {"planned_offer": offer}
    saves = []
    state.save_season = lambda: saves.append(offer["years"])
    preparation = MarketPreparation(state)
    private = preparation.manager.records["row"]
    private["offer"]["years"] = 2
    private["new"] = {"items": []}
    preparation.state.save_season()
    assert saves == [2] and negotiations.row is negotiations.records["row"]
    assert negotiations.row["offer"] is offer is negotiations.row["context"]["planned_offer"]
    private["new"]["items"].append("pending")
    assert negotiations.row["new"]["items"] == []
    preparation.publish()
    assert negotiations.row["new"]["items"] == ["pending"]


def test_market_terminal_publication_does_not_publish_other_offers_or_their_mail(monkeypatch):
    state, _, negotiations, _, _, _, _ = owned(monkeypatch)
    state.email_manager = sys.modules["owned_personnel_adapter"].EmailManager(state)
    negotiations.records = {
        key: {"id": key, "candidate_key": candidate, "status": "talks_open", "offer": {"years": 3}}
        for key, candidate in (("winner", "a"), ("backup", "b"), ("pending", "b"), ("failed", "c"), ("blocked", "c"))}
    negotiations.candidate_deadlines = {key: 114 for key in ("a", "b", "c")}
    preparation = MarketPreparation(state)
    records = preparation.manager.records
    # Native driver AI normalizes multiple candidates before the first signs.
    # Cancelling a backup must not publish its other team's still-pending offer.
    for row in records.values():
        row["offer"]["years"] = 1
    records["winner"]["status"] = "completed"
    records["backup"]["status"] = "cancelled"
    preparation.manager.candidate_deadlines.pop("a")
    for key in ("winner", "pending"):
        with preparation.mail_for(records[key]):
            bound = inspect.signature(type(state.email_manager).send_email).bind(state.email_manager, key, "Owned mail")
            preparation.mail.send(type(state.email_manager).send_email, bound)
    preparation.checkpoint(records["winner"], candidate_scope=True)
    # An individual AI failure also cannot commit that competing offer.
    preparation.checkpoint(records["backup"])
    assert negotiations.records["backup"]["status"] == "cancelled"
    assert negotiations.records["pending"]["offer"]["years"] == 3
    assert negotiations.candidate_deadlines == {"b": 114, "c": 114}
    records["failed"].update(status="failed", failure_reason="Native rejection")
    # Staff AI can reject a different team's offer for the same person inline,
    # before the next offer reaches validation. That completed rejection survives.
    with preparation.mail_for(records["failed"]):
        bound = inspect.signature(type(state.email_manager).send_email).bind(state.email_manager, "failed", "Owned debug")
        preparation.mail.send(type(state.email_manager).send_email, bound)
    preparation.finish({"role": "ai_staff", "refusal": SimpleNamespace(message="Unavailable"),
                        "offer_attempt": (records["blocked"], None)})
    assert negotiations.records["failed"]["failure_reason"] == "Native rejection"
    assert negotiations.records["blocked"]["offer"]["years"] == 3
    assert [message["subject"] for message in state.email_manager.inbox] == ["failed", "winner"]
    assert state.email_manager.urgent_sound_serial == 2


def test_market_keeps_native_fallback_and_stops_unavailable_after_prior_completion(monkeypatch):
    state, driver, negotiations, planner, channel, _, _ = owned(monkeypatch)
    first = {**negotiations.row, "initiated_by": "ai", "offer": {"years": 3}}
    second = {**first, "offer": {"years": 3}}
    negotiations.records = {"first": first, "second": second, "next": dict(first)}
    negotiations.markets = [(["first", "second"], driver), (["next"], SimpleNamespace(contract=None))]
    original_dispatch = type(channel).__call__
    modes = iter(("deny", "allow", "failed"))
    attempts = []

    def dispatch(*args):
        channel.mode = next(modes)
        attempts.append(channel.mode)
        return original_dispatch(*args)

    monkeypatch.setattr(type(channel), "__call__", dispatch)
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    try:
        assert negotiations.resolve_due_decisions() is None
        assert attempts == ["deny", "allow", "failed"]
        assert state.writes == [2, "closed"] and driver.contract == {"salary": 2}
        assert adapter.last_outcome.status == "unavailable" and not adapter._active
    finally:
        adapter.stop()


def test_ai_unavailability_unwinds_nested_uncontested_but_policy_and_native_failure_continue(monkeypatch):
    for mode in ("deny", "allow", "failed"):
        state, driver, negotiations, planner, channel, _, _ = owned(monkeypatch)
        channel.mode, state.native_refuse = mode, True
        channel.message = "Rule says driver will not renew this reserve role contract"
        reports = []
        adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"},
                                                  submit_log=reports.append).install_prepared()
        try:
            def advance():
                result = negotiations.ai_manage_drivers("reserve")
                return result, adapter.last_outcome, getattr(adapter._local, "operation", False)
            with ThreadPoolExecutor(max_workers=1) as worker:
                result, outcome, admitted = worker.submit(advance).result(timeout=3)
            assert result is None and not admitted
            assert state.writes == ({"deny": ["cooldown"], "allow": [2, "cooldown"], "failed": []}[mode])
            assert outcome.status == {"failed": "unavailable"}.get(mode, mode)
            assert driver.contract is None and not adapter._active
            assert len(reports) == (1 if mode == "failed" else 0)
            if reports:
                assert reports[0]["level"] == "ERROR"
                assert reports[0]["scope"] == "personnel.contract" and "Owned handler failed" in reports[0]["detail"]
            if mode != "failed":
                assert negotiations.row["permanent_block"] is (mode == "allow")
                assert negotiations.row["blocked_role"] is None
                assert negotiations.row["failure_reason"] == (channel.message if mode == "deny" else "Native driver will not renew")
        finally:
            adapter.stop()


def test_weekly_worker_policy_refusal_does_not_disable_later_player_signing(monkeypatch):
    state, driver, negotiations, _, channel, _, _ = owned(monkeypatch)
    negotiations.records = {"first": negotiations.row}
    negotiations.markets = [(["first"], driver)]
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    try:
        def advance():
            result = negotiations.resolve_due_decisions()
            return result, getattr(adapter._local, "operation", False)
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert worker.submit(advance).result(timeout=3) == (None, False)
        assert driver.contract is None and adapter.owners == {"owned.rules": "ready"} and not adapter.errors
        channel.mode = "allow"
        assert negotiations.resolve_player_contract_decision("decision", True) == (True, "closed")
        assert driver.contract == {"salary": 2} and not adapter._active
    finally:
        adapter.stop()


def test_driver_guard_cancels_confirmation_keeps_native_exceptions_and_restores(monkeypatch):
    state, driver, negotiations, planner, channel, signing, _ = owned(monkeypatch)
    original = type(state.driver_manager).sign_driver
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    try:
        # Each refused confirmation must remain pending; a native False would
        # otherwise try a backup and close it. Offer preparation must not stick.
        for mode, reason in (("deny", "Owned refusal"), ("failed", "Validation unavailable")):
            channel.mode = mode
            assert negotiations.resolve_player_contract_decision("decision", True) == (False, reason)
            assert adapter.last_outcome.status == ("deny" if mode == "deny" else "unavailable")
            assert "decision" in negotiations.pending and negotiations.row["offer"]["years"] == 3
            assert not state.writes and vars(driver) == {"contract": None} and state.teams == {"target": {}}
        assert adapter.owners == {"owned.rules": "unavailable"}
        # No guard is reached for this new attempt. An old blocked reason may
        # remain on the native record, but it is not this attempt's outcome.
        negotiations.row["native_precheck_failure"] = True
        assert negotiations._execute_offer(negotiations.row, driver) is False
        assert adapter.last_outcome is None
        assert negotiations.row["execution_block_reason"] == "Validation unavailable"
        negotiations.row.pop("native_precheck_failure")
    finally:
        adapter.stop()
    assert type(state.driver_manager).sign_driver is original

    channel = type(channel)()
    channel.mode = "allow"
    adapter = implementation.RegulationPersonnelAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    try:
        assert negotiations.resolve_player_contract_decision("decision", True) == (True, "closed")
        assert state.writes == [2] and negotiations.row["offer"]["years"] == 1
        assert driver.personality == "loyal" and driver.career_stage == "youngster" and state.teams["target"]["prestige"] == 50
        with pytest.raises(ValueError, match="Native failure"):
            state.driver_manager.sign_driver(driver, 0)
        assert state.writes == [2, 0] and adapter.owners == {"owned.rules": "ready"}
        state.native_refuse = True
        negotiations.pending["decision"] = {"status": "pending"}
        assert negotiations.resolve_player_contract_decision("decision", True) == (True, "closed")
        assert state.writes[-2:] == [2, "backup"]
        assert adapter.last_outcome.status == "allow"
        other = type(state.driver_manager)()
        other.state = SimpleNamespace(writes=[], native_refuse=False)
        channel.mode = "deny"
        assert other.sign_driver(SimpleNamespace(contract=None)) is True and other.state.writes == [2]
    finally:
        adapter.stop()
    assert type(state.driver_manager).sign_driver is original


def test_personnel_preflight_rejects_changed_callers_before_assignment(monkeypatch):
    state, driver, negotiations, planner, channel, signing, callers = owned(monkeypatch)
    originals = type(state.driver_manager).sign_driver, type(negotiations)._execute_offer
    callers[-1].signature = "(changed)"
    with pytest.raises(HookError, match="signature changed"):
        implementation.RegulationPersonnelAdapter(state, channel, None)
    assert (type(state.driver_manager).sign_driver, type(negotiations)._execute_offer) == originals


def test_staff_and_driver_share_callers_without_publishing_refused_staff_preparation(monkeypatch):
    # Exercise staff-specific temporary fields through the shared negotiation path.
    state, driver, negotiations, _, channel, _, _ = owned(monkeypatch)
    staff = SimpleNamespace(contract=None)
    negotiations.driver = staff
    negotiations.row = {"candidate_type": "staff", "initiated_by": "player", "offer": {"years": 3},
                        "context": {"entry_id": "native-hint"}}
    original_offer = type(negotiations)._execute_offer
    original_staff = type(state.staff_manager).sign_staff
    adapter = implementation.RegulationPersonnelAdapter(state, channel,
                                              lambda *a: {"selected": "current"}).install_prepared()
    try:
        assert len([name for name in adapter.groups if name.endswith("caller.offer")]) == 1
        for mode, reason in (("deny", "Owned refusal"), ("failed", "Validation unavailable")):
            channel.mode = mode
            assert negotiations.resolve_player_contract_decision("decision", True) == (False, reason)
            assert "decision" in negotiations.pending and vars(staff) == {"contract": None}
            assert not state.writes and state.teams == {"target": {}}
    finally:
        adapter.stop()
    assert type(negotiations)._execute_offer is original_offer and type(state.staff_manager).sign_staff is original_staff

    channel = type(channel)()
    channel.mode = "allow"
    adapter = implementation.RegulationPersonnelAdapter(state, channel,
                                              lambda *a: {"selected": "current"}).install_prepared()
    try:
        assert negotiations.resolve_player_contract_decision("decision", True) == (True, "closed")
        assert staff.contract == {"salary": 2} and staff._oval_target_entry_id == "native-hint"
        assert driver.contract is None and state.writes == ["staff"] and state.teams["target"]["prestige"] == 50
        channel.mode = "deny"
        other = type(state.staff_manager)()
        other.state = SimpleNamespace(writes=[], teams={"target": {}}, prestige_manager=type(state.prestige_manager)(), native_refuse=False)
        assert other.sign_staff(SimpleNamespace(contract=None)) is True and other.state.writes == ["staff"]
    finally:
        adapter.stop()
