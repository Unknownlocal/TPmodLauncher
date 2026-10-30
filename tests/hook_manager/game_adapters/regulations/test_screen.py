# SPDX-License-Identifier: MPL-2.0
"""Extra regulation cards keep native layout, results and cleanup ownership."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.adapter_modules import AdapterModules
from hook_manager.game_adapters.regulations import regulation_screen as presentation
from hook_manager.manager import Manager


@pytest.fixture
def catalogue(monkeypatch):
    module = ModuleType("ui.screens.regulations_screen")
    module.FORMULA_SECTIONS, module.WHITE = (("Native", ("native",)),), (240, 240, 240)
    module.CHROME = {(35, 51, 74): (35, 51, 74), (25, 36, 54): (25, 36, 54)}

    def rect(x, y, w, h):
        return SimpleNamespace(x=x, y=y, w=w, h=h, right=x + w, center=(x + w // 2, y + h // 2))

    class RegulationsScreen:
        def _draw_sections(self, surface, left_rect):
            self.draws += 1
            if self.native_error:
                raise self.native_error
            self.card_hitboxes = [(rect(left_rect.x, left_rect.y, 200, 90), "native")] if self.native_cards else []
            return left_rect.y + (100 if self.native_cards else 0)

        def _draw_regulation_card(self, surface, entry, rect):
            self.card_hitboxes.append((rect, entry["key"]))
            if self.card_error:
                raise ValueError("extra card failed")

    module.RegulationsScreen = RegulationsScreen
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(presentation, "ui_bindings", lambda screen: SimpleNamespace(pygame=SimpleNamespace(
        Rect=rect, draw=SimpleNamespace(line=lambda *args: None, rect=lambda *args, **kwargs: None))))
    font = SimpleNamespace(get_linesize=lambda: 10, size=lambda text: (200, 10),
                           render=lambda *args: SimpleNamespace(get_height=lambda: 10, get_rect=lambda **kwargs: kwargs))
    screen = RegulationsScreen()
    screen.state, screen.draws = SimpleNamespace(game_mode="formula"), 0
    screen.native_cards, screen.native_error, screen.card_error = True, None, False
    screen.entries = {key: {"key": key} for key in ("native", "owned.first", "owned.second", "owned.third")}
    screen.ui_gap = screen.sx = screen.sy = lambda value: value
    screen.card_title_font = screen.micro = screen.tiny = font
    return screen, rect


def test_automatic_cards_and_details_keep_native_drawing_and_palette(catalogue, monkeypatch):
    screen, rect = catalogue
    def decision(self, key, entry):
        return "Team voting"
    def metric(self, surface, rect, label, value, color="native palette"):
        return label, value, color
    monkeypatch.setattr(type(screen), "_decision_label", decision, raising=False)
    monkeypatch.setattr(type(screen), "_draw_metric", metric, raising=False)
    helper = SimpleNamespace(state=screen.state, policies={"owned.first": {"base_chance": .25, "announcement_week": 40}})
    adapter = presentation.RegulationScreenAdapter(lambda message: None, SimpleNamespace(automatic=helper))
    try:
        screen.selected_key = "owned.first"
        assert screen._decision_label("owned.first", {}) == "Change chance 25%"
        assert screen._draw_metric(None, None, "Rule decisions", "Team vote") == ("Change chance", "25%", "native palette")
        assert screen._draw_metric(None, None, "Available options", "2") == ("Announcement week", "41", "native palette")
        assert screen._decision_label("native", {}) == "Team voting"
        screen.selected_key = "native"
        assert screen._draw_metric(None, None, "Rule decisions", "Team vote") == ("Rule decisions", "Team vote", "native palette")
        cached = screen._decision_label
        adapter.stop()
        assert cached("owned.first", {}) == "Team voting"
    finally:
        adapter.stop()


@pytest.mark.parametrize("native_cards,width", [(True, 240), (False, 1200)])
def test_cards_fit_catalogue_and_pack_cleanup_restores_cached_draws(catalogue, native_cards, width):
    screen, rect = catalogue
    original = type(screen)._draw_sections
    reports = []
    adapter = presentation.RegulationScreenAdapter(reports.append)
    host = ModuleType("owned_screen_adapter")
    host.manager = Manager()
    modules = AdapterModules(host, {})
    modules.regulation_screen = adapter
    screen.native_cards = native_cards
    surface = SimpleNamespace(blit=lambda *args: None)
    left = rect(10, 20, width, 300)
    cached = screen._draw_sections
    try:
        bottom = cached(surface=surface, left_rect=left)
        cards = screen.card_hitboxes[int(native_cards):]
        assert [key for _, key in cards] == ["owned.first", "owned.second", "owned.third"]
        assert all(left.x <= card.x and card.x + card.w <= left.x + left.w for card, _ in cards)
        assert all(card.y >= 120 if native_cards else card.y >= 20 for card, _ in cards)
        assert bottom == max(card.y + card.h for card, _ in cards) + 12
        if native_cards:
            assert all(card.x == left.x and card.h == 90 for card, _ in cards)
            assert len({card.y for card, _ in cards}) == 3  # One-column catalogue.
        else:
            assert len({card.x for card, _ in cards}) == 3 and len({card.y for card, _ in cards}) == 1
        assert not reports
        assert modules.stop_regulations(retain=True) and not adapter.closed
        # A new career uses its own loaded entries, without retaining old cards.
        screen.entries = {"owned.reloaded": {"key": "owned.reloaded"}}
        cached(surface, left)
        assert screen.card_hitboxes[-1][1] == "owned.reloaded"
        screen.entries = {"native": {"key": "native"}}
        assert cached(surface, left) == (120 if native_cards else 20)
    finally:
        modules.close()
        host.manager.stop()
    assert type(screen)._draw_sections is original and adapter.closed
    screen.entries["owned.after_close"] = {"key": "owned.after_close"}
    assert cached(surface, left) == (120 if native_cards else 20)
    assert len(screen.card_hitboxes) == int(native_cards)


def test_extra_card_failure_keeps_native_result_and_native_errors_are_not_retried(catalogue):
    screen, rect = catalogue
    reports = []
    adapter = presentation.RegulationScreenAdapter(reports.append)
    cached = screen._draw_sections
    surface, left = SimpleNamespace(blit=lambda *args: None), rect(0, 0, 1000, 500)
    try:
        screen.native_error = ValueError("native draw failed")
        with pytest.raises(ValueError) as raised:
            cached(surface, left)
        assert raised.value is screen.native_error and screen.draws == 1 and not reports
        screen.native_error, screen.card_error = None, True
        assert cached(surface, left) == 100 and screen.draws == 2
        assert [key for _, key in screen.card_hitboxes] == ["native"]
        assert len(reports) == 1 and "extra card failed" in reports[0]["detail"]
        assert cached(surface, left) == 100 and screen.draws == 3 and len(reports) == 1
    finally:
        adapter.stop()
