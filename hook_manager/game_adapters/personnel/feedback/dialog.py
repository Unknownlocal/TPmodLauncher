# SPDX-License-Identifier: MPL-2.0
"""Use the native driver's refusal layout for either personnel kind."""

from .scroll_text import ScrollText


class RefusalDialog:
    # Native driver/staff screens have different popup APIs. Use the driver's
    # layout with common native theme/text/scrollbar helpers so staff feedback
    # works without importing the driver screen or copying its entire UI class.
    def __init__(self, ui):
        self.ui, self.open = ui, False
        self.body = ScrollText(ui)
        self.close = self.cross = None

    def draw(self, screen, surface, person, reason):
        pg, theme = self.ui.pygame, self.ui.theme
        overlay = pg.Surface(surface.get_size(), pg.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        surface.blit(overlay, (0, 0))
        pad = screen.ui_gap(22, minimum=10)
        title = screen.ui_font(27, bold=True, family="Consolas")
        note_font = screen.ui_font(16, bold=True, family="Consolas")
        box, _ = screen.constrained_modal(surface.get_rect(), (screen.dp(650), screen.dp(340)),
                                       minimum_size=(min(340, surface.get_width()), min(220, surface.get_height())))
        pg.draw.rect(surface, (32, 19, 25), box, border_radius=max(1, screen.sy(8)))
        pg.draw.rect(surface, theme.DANGER, box, max(1, screen.sy(1)), border_radius=max(1, screen.sy(8)))
        size = max(screen.dp(32), note_font.get_linesize() + screen.ui_gap(10))
        self.cross = pg.Rect(box.right - pad - size, box.y + pad, size, size)
        self.close = pg.Rect(box.centerx - screen.dp(80), box.bottom - pad - screen.dp(42), screen.dp(160), screen.dp(42))
        for rect, label in ((self.cross, "X"), (self.close, "Close")):
            pg.draw.rect(surface, (115, 41, 51), rect, border_radius=max(1, screen.sy(5)))
            screen.draw_text_block(surface, label, screen.small, rect, (240, 240, 240), align="center", valign="center")
        y = box.y + pad
        heading = pg.Rect(box.x + pad, y, max(1, self.cross.left - box.x - 2 * pad), title.get_linesize())
        screen.draw_text_block(surface, "Contract cannot be signed", title, heading, (255, 215, 218))
        y += heading.height + screen.ui_gap(4, minimum=2)
        name = pg.Rect(box.x + pad, y, box.width - 2 * pad, screen.small.get_linesize())
        screen.draw_text_block(surface, str(person.name), screen.small, name, (240, 240, 240))
        y += name.height + screen.ui_gap(17, minimum=7)
        note = pg.Rect(box.x + pad, self.close.y - note_font.get_linesize() - screen.ui_gap(12, minimum=5),
                       box.width - 2 * pad, note_font.get_linesize())
        pending = (screen._pipeline_record() or {}).get("status") == "awaiting_player_confirmation"
        text = "The agreed contract is still awaiting your decision." if pending else "No contract was signed."
        screen.draw_text_block(surface, text, note_font,
                               note, theme.TEXT_MUTED, align="center")
        body = pg.Rect(box.x + pad, y, box.width - 2 * pad, max(1, note.y - screen.ui_gap(10) - y))
        pg.draw.rect(surface, (75, 29, 38), body, border_radius=max(1, screen.sy(6)))
        pg.draw.rect(surface, theme.DANGER, body, max(1, screen.sy(1)), border_radius=max(1, screen.sy(6)))
        self.body.draw(screen, surface, body, [(reason, (255, 173, 179))], screen.small)

    def handle_event(self, screen, event):
        pg = self.ui.pygame
        if not self.open:
            return False
        if event.type == pg.KEYDOWN and event.key in (pg.K_ESCAPE, pg.K_RETURN, pg.K_SPACE):
            self.open = False
        elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
            pos = screen.window_to_canvas(event.pos)
            if pos is not None and any(rect is not None and rect.collidepoint(pos) for rect in (self.cross, self.close)):
                self.open = False
        self.body.handle_event(screen, event)
        return True
