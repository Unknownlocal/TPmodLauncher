# SPDX-License-Identifier: MPL-2.0
"""Personnel UI ownership, refusal persistence and independent failure handling."""

import sys
from types import ModuleType, SimpleNamespace
from typing import Dict

import pytest

from hook_manager.game_adapters.personnel.feedback import adapter as feedback
from hook_manager.game_adapters.regulations.regulation_personnel import RegulationPersonnelAdapter
from tests.hook_manager.negotiation_support import negotiation_state as owned
from tests.hook_manager.approach_support import approach_setup


def screens(monkeypatch):
    class Bar:
        def __init__(self, **kwargs):
            pass

        def handle_event(self, *args):
            return None

    ui = SimpleNamespace(scrollbar=Bar, chrome={}, pygame=SimpleNamespace(
        KEYDOWN=1, K_ESCAPE=2, K_RETURN=3, K_SPACE=4, MOUSEBUTTONDOWN=5, MOUSEWHEEL=6))
    monkeypatch.setattr(feedback, "feedback_bindings", lambda screen, kind: ui)
    classes = {}
    for kind in ("driver", "staff"):
        module = ModuleType("ui.screens." + kind + "_negotiation_screen")

        def handle_event(self, event):
            if event == "approach":
                self._confirm_begin_approach()
            if event == "sign" or getattr(event, "action", None) == "sign":
                ok, self.message = self.state.personnel_negotiation_manager.resolve_player_contract_decision("decision", True)
                if not ok and self.kind == "driver":
                    self._approach_failure_open = True

        def draw(self, surface):
            self.draws += 1
            raise ValueError("Owned native draw error")

        def confirm(self):
            ok, self.message = self.approach_result
            self.action_feedback(ok)
            if getattr(self, "approach_error", False):
                raise ValueError("Owned native approach error")
            self._approach_confirm_open = False

        def action_feedback(self, successful):
            self.sounds.append(successful)

        def failure(self, reason: str, *, title='Approach cannot begin', note='No approach was started or personnel slot used.') -> None:
            self.message, self._approach_failure_reason = reason, reason
            self._approach_failure_title, self._approach_failure_note = title, note
            self._approach_failure_open = True

        cls = type(kind.title() + "NegotiationScreen", (), {
            "handle_event": handle_event, "draw": draw,
            "_confirm_begin_approach": confirm, "action_feedback": action_feedback,
            "_show_approach_failure": failure})
        setattr(module, cls.__name__, cls)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        classes[kind] = cls
    return classes


def screen_for(cls, state, kind):
    screen = cls()
    screen.state, screen.kind, screen.message, screen.draws = state, kind, "", 0
    screen._approach_failure_open = False
    screen.sounds = []
    screen.last_interest_breakdown = []
    screen._section_header = lambda *args, **kwargs: None
    screen._draw_clipped = screen._draw_action_buttons = lambda *args, **kwargs: None
    screen._button = lambda *args, **kwargs: None
    screen.get_mouse_canvas_pos = lambda: None
    screen.window_to_canvas = lambda pos: pos
    return screen


def inbox_screen(monkeypatch, state):
    screens(monkeypatch)
    monkeypatch.setattr(feedback, "ui_bindings", lambda cls: feedback.feedback_bindings(cls, "inbox"))
    module = ModuleType("ui.screens.mailbox_screen")

    class MailboxScreen:
        def _execute_action(self, action: Dict) -> bool:
            self.executions += 1
            if self.error:
                raise ValueError("Native inbox error")
            if self.native_refuse:
                return False
            ok, _ = self.state.personnel_negotiation_manager.resolve_player_contract_decision(
                action["decision_id"], action["choice"] == "accept")
            return bool(ok)

        def handle_event(self, event):
            if getattr(event, "type", None) == 7:
                ok = self._execute_action(self.action)
                self.sounds.append(ok)
                if ok:
                    self.action["completed"] = True

        def draw(self, surface):
            self.draws += 1
            return "inbox drawn"

    module.MailboxScreen = MailboxScreen
    monkeypatch.setitem(sys.modules, module.__name__, module)
    screen = MailboxScreen()
    screen.state, screen.executions, screen.draws, screen.sounds = state, 0, 0, []
    screen.native_refuse, screen.error = False, False
    screen.action = {"type": "personnel_contract_decision", "decision_id": "decision", "choice": "accept", "completed": False}
    screen.window_to_canvas = lambda pos: pos
    return screen


def test_staff_approach_dialog_preserves_native_reason_sound_and_helper_ownership(monkeypatch, approach_setup):
    classes = screens(monkeypatch)
    _, _, adapter, _, _, _ = approach_setup
    state = adapter.approach.state
    adapter.poll()  # Complete the approach-only owner's deferred staff UI import.
    screen = screen_for(classes["staff"], state, "staff")
    reason = "Nationality must match Audi (german)."
    screen.approach_result = False, reason
    try:
        screen.handle_event("approach")
        notice = adapter.feedback.notices[screen]
        assert notice.dialog.open and notice.approach_refusal and notice.reason == screen.message == reason
        assert screen.sounds == [False] and "action_feedback" not in vars(screen)
        screen.handle_event(SimpleNamespace(type=1, key=2))
        assert not notice.dialog.open and screen.message == reason
        # Native instance overrides must also survive temporary observation.
        override = lambda successful: screen.sounds.append(successful)
        screen.action_feedback = override
        screen.approach_result = True, "Approach started."
        screen.handle_event("approach")
        assert notice.reason is None and not notice.dialog.open and screen.sounds == [False, True]
        assert screen.action_feedback is override
        screen.approach_error = True
        with pytest.raises(ValueError, match="Owned native approach error"):
            screen.handle_event("approach")
        assert screen.action_feedback is override and screen.sounds == [False, True, True]
    finally:
        adapter.stop()


def test_driver_native_approach_popup_reports_one_error_and_restores_on_cleanup(monkeypatch, approach_setup):
    classes = screens(monkeypatch)
    _, _, adapter, _, _, _ = approach_setup
    adapter.poll()
    screen = screen_for(classes["driver"], adapter.approach.state, "driver")
    reason = "Nationality must match Audi (german)."
    escaped = screen._show_approach_failure
    try:
        assert escaped(reason, title="Approach rejected", note="No slot used.") is None
        assert screen._approach_failure_open and screen.message == screen._approach_failure_reason == reason
        assert (screen._approach_failure_title, screen._approach_failure_note) == ("Approach rejected", "No slot used.")
        assert screen.sounds == [False]
    finally:
        adapter.stop()
    escaped(reason)
    assert screen.sounds == [False]  # Cached wrappers stop supplying feedback after cleanup.


def test_personnel_refusal_dialog_closes_without_losing_inline_reason(monkeypatch):
    classes = screens(monkeypatch)
    for kind in ("driver", "staff"):
        state, person, negotiations, planner, channel, _, _ = owned(monkeypatch)
        if kind == "staff":
            negotiations.row["candidate_type"] = "staff"
        cls = classes[kind]
        originals = cls.handle_event, cls.draw
        adapter = RegulationPersonnelAdapter(state, channel, lambda *args: {"selected": "current"}).install_prepared()
        screen = screen_for(cls, state, kind)
        try:
            cls.handle_event(self=screen, event="sign")
            notice = adapter.feedback.notices[screen]
            assert notice.reason == screen.message == "Owned refusal" and notice.dialog.open
            assert "decision" in negotiations.pending and not state.writes
            screen.handle_event(SimpleNamespace(type=1, key=2))
            screen.handle_event("unrelated")
            assert notice.reason == "Owned refusal" and not notice.dialog.open
            with pytest.raises(ValueError, match="Owned native draw error"):
                screen.draw(SimpleNamespace(get_clip=lambda: None, set_clip=lambda clip: None))
            assert screen.draws == 1 and screen.message == "Owned refusal"
            assert adapter.feedback.groups["personnel.ui." + kind]["state"] == "active"
            channel.mode = "allow"
            notice.inline.rect = object()
            screen.handle_event(SimpleNamespace(type=7, action="sign"))
            assert notice.reason is None and notice.inline.rect is None and person.contract is not None
            foreign = screen_for(cls, SimpleNamespace(personnel_negotiation_manager=SimpleNamespace(
                resolve_player_contract_decision=lambda *args: (False, "Foreign"))), kind)
            foreign.handle_event("sign")
            assert foreign not in adapter.feedback.notices and foreign.message == "Foreign"
        finally:
            adapter.stop()
        assert (cls.handle_event, cls.draw) == originals


def test_ui_failure_leaves_signing_active_and_does_not_retry_native_draw(monkeypatch):
    classes = screens(monkeypatch)
    state, person, negotiations, planner, channel, _, _ = owned(monkeypatch)
    binding = feedback.feedback_bindings

    def compatible(cls, kind):
        if kind == "driver":
            raise RuntimeError("Owned incompatible UI helper")
        return binding(cls, kind)

    monkeypatch.setattr(feedback, "feedback_bindings", compatible)
    reports = []
    adapter = RegulationPersonnelAdapter(state, channel, lambda *args: {"selected": "current"}, submit_log=reports.append).install_prepared()
    screen = screen_for(classes["staff"], state, "staff")
    try:
        assert len(reports) == 1 and "UI COMPATIBILITY FAILED" in reports[0]["detail"]
        assert adapter.feedback.groups["personnel.ui.staff"]["state"] == "active"
        screen.handle_event("unrelated")
        # Replace only this owned native fixture body before installing a fresh
        # UI group, so its normal return can precede a presentation failure.
        adapter.feedback.stop()

        def draw(self, surface):
            self.draws += 1
            return "drawn"

        monkeypatch.setattr(classes["staff"], "draw", draw)
        factory = feedback.PersonnelNotice
        for phase in ("construction", "before", "after"):
            adapter.feedback = feedback.PersonnelFeedback(adapter, state, reports.append)

            def failed(*args):
                if phase == "after":
                    args[-1]()
                raise RuntimeError("Owned response drawing failure")

            if phase == "construction":
                monkeypatch.setattr(feedback, "PersonnelNotice", failed)
            else:
                screen.handle_event("unrelated")
                monkeypatch.setattr(adapter.feedback.notices[screen], "draw", failed)
            before = screen.draws
            retained = screen.draw
            assert retained(None) == "drawn" and screen.draws == before + 1
            assert adapter.feedback.groups["personnel.ui.staff"]["state"] == "failed"
            count = len(reports)
            assert retained(None) == "drawn" and screen.draws == before + 2 and len(reports) == count
            assert all(group["state"] == "active" for group in adapter.groups.values())
            assert adapter.owners == {"owned.rules": "ready"}
            monkeypatch.setattr(feedback, "PersonnelNotice", factory)
            adapter.feedback.stop()
    finally:
        adapter.stop()


@pytest.mark.parametrize("kind", ["driver", "staff"])
def test_inbox_refusals_keep_approval_pending_and_fresh_failures_do_not_reuse_messages(monkeypatch, kind):
    state, person, negotiations, _, channel, _, _ = owned(monkeypatch)
    negotiations.row["candidate_type"] = kind
    negotiations.pending["decision"]["candidate_name"] = "Candidate"
    screen = inbox_screen(monkeypatch, state)
    adapter = RegulationPersonnelAdapter(state, channel, lambda *args: {"selected": "current"}).install_prepared()
    try:
        screen.handle_event(SimpleNamespace(type=7))
        notice = adapter.feedback.notices[screen]
        assert notice.dialog.open and notice.reason == "Owned refusal" and notice.candidate == "Candidate"
        assert screen.executions == 1 and screen.sounds == [False]
        assert not screen.action["completed"] and "decision" in negotiations.pending and not state.writes
        screen.handle_event(SimpleNamespace(type=1, key=2))
        assert not notice.dialog.open and screen.executions == 1
        screen.native_refuse = True
        screen.handle_event(SimpleNamespace(type=7))
        assert notice.reason is None and not notice.dialog.open and not screen.action["completed"]
        screen.native_refuse = False
        channel.validation_ready = False
        screen.handle_event(SimpleNamespace(type=7))
        assert notice.dialog.open and notice.reason == "Validation unavailable" and not state.writes
        screen.handle_event(SimpleNamespace(type=1, key=2))
        channel.validation_ready, channel.mode = True, "allow"
        screen.handle_event(SimpleNamespace(type=7))
        assert person.contract is not None and screen.action["completed"] and screen.sounds == [False, False, False, True]
        assert notice.reason is None and not notice.dialog.open
        assert getattr(adapter._local, "personnel_screen", None) is None
    finally:
        adapter.stop()
    assert not adapter.feedback.notices


def test_inbox_decline_exception_and_retired_wrapper_preserve_the_caller_scope(monkeypatch):
    state, _, negotiations, _, channel, _, _ = owned(monkeypatch)
    screen = inbox_screen(monkeypatch, state)
    adapter = RegulationPersonnelAdapter(state, channel, lambda *args: {"selected": "current"}).install_prepared()
    try:
        # A native decline may try another offer. Its denial is not an acceptance refusal.
        adapter._local.personnel_screen = screen
        assert screen._execute_action(dict(screen.action, choice="decline"))
        notice = adapter.feedback.notices[screen]
        assert not notice.dialog.open and state.writes == ["backup"]
        assert adapter._local.personnel_screen is screen
        screen.error = True
        before = screen.executions
        with pytest.raises(ValueError, match="Native inbox error"):
            screen._execute_action(screen.action)
        assert screen.executions == before + 1 and adapter._local.personnel_screen is screen
        assert adapter.feedback.groups["personnel.ui.inbox"]["state"] == "active"
        adapter._local.personnel_screen = None
        screen.error = False
        negotiations.pending["decision"] = {"status": "pending"}
        retained = screen._execute_action
        adapter.feedback.stop()
        assert not retained(screen.action) and not adapter.feedback.notices
        assert adapter.owners == {"owned.rules": "ready"}
    finally:
        adapter.stop()


def test_inbox_presentation_failure_keeps_validation_and_native_results(monkeypatch):
    state, _, _, _, channel, _, _ = owned(monkeypatch)
    screen = inbox_screen(monkeypatch, state)
    reports = []
    adapter = RegulationPersonnelAdapter(state, channel, lambda *args: {"selected": "current"}, submit_log=reports.append).install_prepared()
    try:
        screen.handle_event(SimpleNamespace(type=7))
        notice = adapter.feedback.notices[screen]
        def failed_draw(*args, **kwargs):
            raise RuntimeError("Inbox presentation failed")
        monkeypatch.setattr(notice.dialog, "draw", failed_draw)
        before = screen.draws
        retained = screen.draw
        assert retained(None) == "inbox drawn" and screen.draws == before + 1
        assert adapter.feedback.groups["personnel.ui.inbox"]["state"] == "failed"
        assert not adapter.feedback.notices and adapter.owners == {"owned.rules": "ready"}
        assert len(reports) == 1 and all(group["state"] == "active" for group in adapter.groups.values())
        assert retained(None) == "inbox drawn" and len(reports) == 1
        # A failure while recording a refusal must also leave the actual refusal intact.
        adapter.feedback.stop()
        adapter.feedback = feedback.PersonnelFeedback(adapter, state, reports.append)
        screen.draw(None)
        notice = adapter.feedback.notices[screen]
        monkeypatch.setattr(notice, "refuse", failed_draw)
        assert not screen._execute_action(screen.action)
        assert not adapter.feedback.notices and adapter.owners == {"owned.rules": "ready"}
        assert not state.writes and not screen.action["completed"]
    finally:
        adapter.stop()
