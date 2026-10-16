# SPDX-License-Identifier: MPL-2.0
"""Owned options interface with minimal geometry and input, independent of pygame."""

from types import SimpleNamespace


class Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = x, y, w, h

    left = property(lambda self: self.x)
    top = property(lambda self: self.y)
    right = property(lambda self: self.x + self.w)
    bottom = property(lambda self: self.y + self.h)
    center = property(lambda self: (self.x + self.w // 2, self.y + self.h // 2))

    def collidepoint(self, pos):
        return self.x <= pos[0] < self.right and self.y <= pos[1] < self.bottom


class Font:
    def get_linesize(self):
        return 20

    def size(self, text):
        return len(text) * 10, 20


input_calls = []
pygame = SimpleNamespace(
    Rect=Rect, MOUSEWHEEL=1, MOUSEBUTTONDOWN=2, TEXTINPUT=3, KEYDOWN=4, MOUSEMOTION=5,
    K_ESCAPE=10, K_TAB=11, K_RETURN=12, K_KP_ENTER=13, K_r=14, K_a=15, K_v=16,
    K_BACKSPACE=17, K_DELETE=18, K_LEFT=19, K_RIGHT=20, K_HOME=21, K_END=22,
    KMOD_SHIFT=1, KMOD_CTRL=2, error=RuntimeError,
    key=SimpleNamespace(start_text_input=lambda: input_calls.append("start"),
                        stop_text_input=lambda: input_calls.append("stop")),
)
OPTION_TABS = [("game", "Game"), ("graphics", "Graphics")]
native_calls = []


class OptionsScreen:
    def __init__(self):
        self.active_tab = "game"
        self.small = Font()
        self._layout_cache = self._chrome_cache = "cached"
        self.tab_rects = []

    def draw(self, surface):
        native_calls.append(("draw", surface))
        return "native draw"

    def handle_event(self, event):
        native_calls.append(("event", event))
        return "native event"

    def _layout_rects(self):
        return {"panel": Rect(10, 10, 780, 580), "left": Rect(20, 110, 370, 360),
                "list_view": Rect(20, 110, 370, 360), "footer": Rect(20, 510, 760, 60)}

    @staticmethod
    def ui_gap(value):
        return value

    @staticmethod
    def dp(value):
        return value

    @staticmethod
    def window_to_canvas(pos):
        return pos

    @staticmethod
    def get_mouse_canvas_pos():
        return 0, 0


ORIGINAL_METHODS = {name: getattr(OptionsScreen, name) for name in ("draw", "handle_event")}
