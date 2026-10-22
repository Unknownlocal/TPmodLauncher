# SPDX-License-Identifier: MPL-2.0
"""Shared scrolling follows the caller's font without personnel screen fields."""

from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.ui import ScrollText


def test_scroll_text_uses_supplied_font_and_restores_clipping(monkeypatch):
    class Rect:
        x = y = 0
        width, height = 120, 40

        def clip(self, other):
            return self

        def collidepoint(self, pos):
            return True

    class Bar:
        visible = True

        def __init__(self, **kwargs):
            pass

        def sync(self, rect, offset, maximum):
            self.maximum = maximum

        def set_scroll(self, value):
            return max(0, min(value, self.maximum))

        def draw(self, surface):
            pass

    class Font:
        def get_linesize(self):
            return 10

        def render(self, *args):
            return "rendered"

    class Surface:
        def __init__(self):
            self.clip = Rect()

        def get_rect(self):
            return Rect()

        def get_clip(self):
            return self.clip

        def set_clip(self, clip):
            self.clip = clip

        def blit(self, *args):
            pass

    wraps = []

    def wrap(text, font, width):
        wraps.append(text)
        return text.splitlines()

    ui = SimpleNamespace(scrollbar=Bar, chrome={}, wrap=wrap,
                         pygame=SimpleNamespace(MOUSEWHEEL=1, MOUSEBUTTONDOWN=2))
    # There is deliberately no screen.small, candidate or negotiation record.
    screen = SimpleNamespace(ui_gap=lambda *args, **kwargs: 4, get_mouse_canvas_pos=lambda: (10, 10))
    widget, font, surface = ScrollText(ui), Font(), Surface()
    original_clip = surface.clip
    blocks = [("\n".join(str(i) for i in range(10)), (240, 240, 240))]
    widget.draw(screen, surface, Rect(), blocks, font)
    assert widget.handle_event(screen, SimpleNamespace(type=1, y=-1))
    assert widget.offset == 30 and surface.clip is original_clip
    widget.draw(screen, surface, Rect(), blocks, font)
    assert len(wraps) == 1

    def failed(*args):
        raise ValueError("Owned font error")

    monkeypatch.setattr(font, "render", failed)
    with pytest.raises(ValueError, match="Owned font error"):
        widget.draw(screen, surface, Rect(), blocks, font)
    assert surface.clip is original_clip
