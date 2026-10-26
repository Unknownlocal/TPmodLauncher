# SPDX-License-Identifier: MPL-2.0
"""Show loaded regulation categories omitted by the native screen's fixed groups."""

from functools import update_wrapper
import threading

from ...callables import CallableTarget, method_group
from ...manager import Manager
from ..ui import ui_bindings


class RegulationScreenAdapter(Manager):
    """Append native cards without replacing the screen's input or inspector."""

    def __init__(self, report):
        """Observe the screen and send later errors to the pack's notice queue."""
        super().__init__(lambda record: None)
        self.report = report
        # Opening this screen is optional. Observe its normal import instead of
        # importing it during career loading or making readiness wait for it.
        self.observe("ui.screens.regulations_screen", self._ready)

    def _report(self, error):
        super()._report(error)
        self.report("REGULATIONS UI COMPATIBILITY FAILED catalogue: " + " ".join(str(error).split())[:512]
                    + "; custom cards unavailable; continuing native game")

    def _ready(self, module):
        """Check the loaded drawing helpers before changing the single entry point."""
        screen = module.RegulationsScreen
        method_group(screen, {"_draw_sections": "(self, surface, left_rect)",
                              "_draw_regulation_card": "(self, surface, entry, rect)"})
        self.ui, self.native = ui_bindings(screen), module
        target = CallableTarget("regulations.ui.catalogue", module.__name__, (screen.__name__,),
                                "_draw_sections", "(self, surface, left_rect)")
        self.approve(target)
        self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "after"}])
        return True

    def _wrapper(self, name, binding, target, original, signature):
        """Keep native results on UI failure and preserve native exceptions."""
        adapter, group = self, self.groups[name]

        def wrapped(self, surface, left_rect):
            if group["state"] != "active" or threading.current_thread() is not threading.main_thread():
                return original(self, surface, left_rect)
            # Native errors propagate without a second draw. Only our appended
            # presentation can disable this optional integration.
            bottom = original(self, surface, left_rect)
            hitboxes, count = None, 0
            try:
                if group["state"] != "active" or getattr(self.state, "game_mode", "formula") != "formula":
                    return bottom
                hitboxes, count = self.card_hitboxes, len(self.card_hitboxes)
                return adapter._draw_additional(self, surface, left_rect, bottom)
            except Exception as error:
                # A card can fail after registering its hitbox. Keep only native
                # targets and the native scroll extent for this frame. The next
                # normal frame clears any partially drawn extra pixels.
                if hitboxes is not None:
                    del hitboxes[count:]
                adapter._disable(name, error)
                return bottom

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped

    def _draw_additional(self, screen, surface, left_rect, bottom):
        """Draw one extra group and return its bottom for the native scrollbar.

        Native card drawing supplies labels, options, icons and click targets.
        Its caller already owns clipping, selection and the detail panel.
        """
        native_keys = {key for _, keys in self.native.FORMULA_SECTIONS for key in keys}
        entries = [entry for key, entry in screen.entries.items() if key not in native_keys]
        if not entries:
            return bottom
        # A sparse native group may draw only one column on a wide screen, so
        # hitboxes alone cannot tell us how many columns are available.
        gap = screen.ui_gap(9)
        minimum = max(screen.sx(300), screen.card_title_font.size("Min. Chassis Weight")[0]
                      + screen.sx(74) + screen.ui_gap(24))
        columns = max(1, min(4, (left_rect.w + gap) // max(1, minimum + gap)))
        width = max(1, (left_rect.w - gap * (columns - 1)) // columns)
        # Reuse the final native height where possible. Custom-only catalogues
        # need the same font-based minimum without an existing card to measure.
        height = (screen.card_hitboxes[0][0].h if screen.card_hitboxes else
                  max(screen.sy(146), screen.card_title_font.get_linesize() * 2
                      + screen.tiny.get_linesize() * 2 + screen.micro.get_linesize() * 3
                      + screen.ui_gap(68)))
        # The original heading loop indexes 'ABC'. Draw our heading afterwards
        # rather than modifying its section list and breaking that assumption.
        # Match its divider, badge and palette lookups for the fourth section.
        pygame, chrome = self.ui.pygame, self.native.CHROME
        line_y = bottom + screen.sy(9)
        pygame.draw.line(surface, chrome[(35, 51, 74)], (left_rect.x, line_y),
                         (left_rect.x + left_rect.w, line_y), max(1, screen.sy(1)))
        badge = pygame.Rect(left_rect.x + screen.sx(9), bottom, screen.sx(22), screen.sy(20))
        pygame.draw.rect(surface, chrome[(25, 36, 54)], badge, border_radius=max(1, screen.sy(4)))
        letter = screen.micro.render("D", True, self.native.WHITE)
        surface.blit(letter, letter.get_rect(center=badge.center))
        heading = screen.micro.render("ADDITIONAL REGULATIONS", True, self.native.WHITE)
        surface.blit(heading, (badge.right + screen.sx(8), bottom + screen.sy(2)))
        y = bottom + max(screen.sy(28), heading.get_height() + screen.ui_gap(8))
        for index, entry in enumerate(entries):
            rect = self.ui.pygame.Rect(left_rect.x + index % columns * (width + gap),
                                       y + index // columns * (height + screen.sy(8)), width, height)
            screen._draw_regulation_card(surface, entry, rect)
        rows = (len(entries) + columns - 1) // columns
        return y + rows * height + (rows - 1) * screen.sy(8) + screen.sy(12)
