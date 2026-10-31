# SPDX-License-Identifier: MPL-2.0
"""Check the common native drawing helpers without importing game modules."""

import sys
from types import SimpleNamespace

from ...callables import method_group
from ...manager import HookError


def ui_bindings(screen):
    # Reusing a widget must not change the game's import order. The consumer
    # waits for its native UI to load, then checks the helpers it will reuse.
    modules = {}
    for name in ("pygame", "ui.theme", "ui.color_packs", "ui.responsive_layout", "ui.widgets.scrollbar"):
        module = sys.modules.get(name)
        if module is None or getattr(getattr(module, "__spec__", None), "_initializing", False):
            raise HookError("UI components need completed native imports: " + name)
        modules[name] = module
    # These helpers are inherited from BaseScreen; no personnel methods or
    # candidate/contract fields are needed by the shared components.
    method_group(screen, {
        "ui_gap": "(self, value, *, minimum=0)", "dp": "(self, value, *, minimum=0)", "sy": "(self, value)",
        "ui_font": "(self, design_size, *, bold=False, italic=False, family=None, minimum=1, floor=True)",
        "constrained_modal": "(viewport, desired_size, *, max_width_ratio=0.9, max_height_ratio=0.85, minimum_size=(1, 1))",
        "draw_text_block": "(self, surface, text, font, rect, color, *, align='left', valign='top', line_gap=0, max_lines=None)",
        "window_to_canvas": "(self, pos)", "get_mouse_canvas_pos": "(self)",
    })
    layout, scroll = modules["ui.responsive_layout"], modules["ui.widgets.scrollbar"]
    method_group(layout, {"wrap_text": "(text: 'Any', font: 'pygame.font.Font', max_width: 'int') -> 'List[str]'"})
    method_group(scroll.VerticalScrollbar, {
        "__init__": "(self, width: 'int' = 8, margin: 'int' = 6, align: 'str' = 'outside', allow_content_drag: 'bool' = False, ui_scale: 'float' = 1.0, color_palette: 'object' = None) -> None",
        "sync": "(self, panel_rect: 'pygame.Rect', scroll_value: 'float', max_scroll: 'float', *, align: 'Optional[str]' = None, top_padding: 'int' = 0, bottom_padding: 'int' = 0) -> 'None'",
        "draw": "(self, surface: 'pygame.Surface') -> 'None'",
        "handle_event": "(self, event: 'pygame.event.Event', to_canvas) -> 'Optional[float]'",
        "set_scroll": "(self, value: 'float') -> 'float'",
    })
    return SimpleNamespace(pygame=modules["pygame"], theme=modules["ui.theme"].UITheme,
                           chrome=modules["ui.color_packs"].CHROME, wrap=layout.wrap_text,
                           scrollbar=scroll.VerticalScrollbar)
