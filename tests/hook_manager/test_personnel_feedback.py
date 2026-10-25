# SPDX-License-Identifier: MPL-2.0
"""Personnel UI ownership, refusal persistence and independent failure handling."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.personnel.feedback import adapter as feedback
from hook_manager.game_adapters.regulations.personnel_adapter import PersonnelAdapter
from tests.hook_manager.test_personnel_adapter import owned


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
            if event == "sign" or getattr(event, "action", None) == "sign":
                ok, self.message = self.state.personnel_negotiation_manager.resolve_player_contract_decision("decision", True)
                if not ok and self.kind == "driver":
                    self._approach_failure_open = True

        def draw(self, surface):
            self.draws += 1
            raise ValueError("Owned native draw error")

        cls = type(kind.title() + "NegotiationScreen", (), {"handle_event": handle_event, "draw": draw})
        setattr(module, cls.__name__, cls)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        classes[kind] = cls
    return classes


def screen_for(cls, state, kind):
    screen = cls()
    screen.state, screen.kind, screen.message, screen.draws = state, kind, "", 0
    screen._approach_failure_open = False
    screen.last_interest_breakdown = []
    screen._section_header = lambda *args, **kwargs: None
    screen._draw_clipped = screen._draw_action_buttons = lambda *args, **kwargs: None
    screen._button = lambda *args, **kwargs: None
    screen.get_mouse_canvas_pos = lambda: None
    screen.window_to_canvas = lambda pos: pos
    return screen


def test_personnel_refusal_dialog_closes_without_losing_inline_reason(monkeypatch):
    classes = screens(monkeypatch)
    for kind in ("driver", "staff"):
        state, person, negotiations, planner, channel, _, _ = owned(monkeypatch)
        if kind == "staff":
            negotiations.row["candidate_type"] = "staff"
        cls = classes[kind]
        originals = cls.handle_event, cls.draw
        adapter = PersonnelAdapter(state, channel, lambda *args: {"selected": "current"}).install_prepared()
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
    adapter = PersonnelAdapter(state, channel, lambda *args: {"selected": "current"}, report=reports.append).install_prepared()
    screen = screen_for(classes["staff"], state, "staff")
    try:
        assert len(reports) == 1 and "UI COMPATIBILITY FAILED" in reports[0]
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
