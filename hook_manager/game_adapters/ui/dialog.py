# SPDX-License-Identifier: MPL-2.0
"""Show caller-supplied messages in a bounded native-style dialog."""

from .scroll_text import ScrollText


class ScrollableDialog:
    def __init__(self, ui):
        self.ui, self.open = ui, False
        self.body = ScrollText(ui)
        self.close = self.cross = None

    def draw(self, screen, surface, *, title, body, subtitle="", footer="", style="error", font=None):
        """Draw a dimmed backdrop and a dialog with a scrollable message body.

        The caller supplies the text and calls this only while the dialog is open.
        Subtitle/footer rows are optional; Close and X remain outside the body.
        ``style`` selects error colors or ordinary info colors. ``screen`` supplies
        scaled layout and fonts; ``font`` overrides the body and button font.
        Refreshes the scroll area and button hit rectangles for ``handle_event``.
        """
        pg, theme = self.ui.pygame, self.ui.theme
        # Error dialogs use the native refusal palette; other styles use the
        # ordinary game chrome.
        if style == "error":
            background, border, fill, button = (32, 19, 25), theme.DANGER, (75, 29, 38), (115, 41, 51)
            heading_color, text_color = (255, 215, 218), (255, 173, 179)
        elif style == "info":
            background, border = self.ui.chrome[theme.PANEL], self.ui.chrome[theme.PRIMARY]
            fill, button = self.ui.chrome[theme.PANEL_ALT], border
            heading_color = text_color = theme.TEXT
        else:
            raise ValueError("Unsupported dialog style: " + str(style))
        font = font if font is not None else screen.ui_font(22)
        overlay = pg.Surface(surface.get_size(), pg.SRCALPHA)
        overlay.fill((0, 0, 0, 190))
        surface.blit(overlay, (0, 0))
        pad = screen.ui_gap(22, minimum=10)
        title_font = screen.ui_font(27, bold=True, family="Consolas")
        note_font = screen.ui_font(16, bold=True, family="Consolas")
        box, _ = screen.constrained_modal(surface.get_rect(), (screen.dp(650), screen.dp(340)),
                                       minimum_size=(min(340, surface.get_width()), min(220, surface.get_height())))
        pg.draw.rect(surface, background, box, border_radius=max(1, screen.sy(8)))
        pg.draw.rect(surface, border, box, max(1, screen.sy(1)), border_radius=max(1, screen.sy(8)))
        size = max(screen.dp(32), note_font.get_linesize() + screen.ui_gap(10))
        self.cross = pg.Rect(box.right - pad - size, box.y + pad, size, size)
        self.close = pg.Rect(box.centerx - screen.dp(80), box.bottom - pad - screen.dp(42), screen.dp(160), screen.dp(42))
        for rect, label in ((self.cross, "X"), (self.close, "Close")):
            pg.draw.rect(surface, button, rect, border_radius=max(1, screen.sy(5)))
            screen.draw_text_block(surface, label, font, rect, (240, 240, 240), align="center", valign="center")
        y = box.y + pad
        heading = pg.Rect(box.x + pad, y, max(1, self.cross.left - box.x - 2 * pad), title_font.get_linesize())
        screen.draw_text_block(surface, title, title_font, heading, heading_color)
        y += heading.height + screen.ui_gap(4, minimum=2)
        # Only reserve space for optional rows when supplied. The body fills
        # the remaining area above the fixed dismissal controls.
        if subtitle:
            name = pg.Rect(box.x + pad, y, box.width - 2 * pad, font.get_linesize())
            screen.draw_text_block(surface, subtitle, font, name, (240, 240, 240))
            y += name.height + screen.ui_gap(17, minimum=7)
        bottom = self.close.y
        if footer:
            note = pg.Rect(box.x + pad, self.close.y - note_font.get_linesize() - screen.ui_gap(12, minimum=5),
                           box.width - 2 * pad, note_font.get_linesize())
            screen.draw_text_block(surface, footer, note_font, note, theme.TEXT_MUTED, align="center")
            bottom = note.y
        body_rect = pg.Rect(box.x + pad, y, box.width - 2 * pad, max(1, bottom - screen.ui_gap(10) - y))
        pg.draw.rect(surface, fill, body_rect, border_radius=max(1, screen.sy(6)))
        pg.draw.rect(surface, border, body_rect, max(1, screen.sy(1)), border_radius=max(1, screen.sy(6)))
        self.body.draw(screen, surface, body_rect, [(body, text_color)], font)

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
