# SPDX-License-Identifier: MPL-2.0
"""Keep all response lines reachable inside a fixed native UI rectangle."""


class ScrollText:
    def __init__(self, ui):
        self.ui = ui
        self.bar = ui.scrollbar(align="right_inside", color_palette=ui.chrome)
        self.offset, self.key, self.lines = 0.0, None, []
        self.rect = None

    def reset(self):
        self.offset = 0.0

    def draw(self, screen, surface, rect, blocks, font):
        self.rect = rect.clip(surface.get_rect()).clip(surface.get_clip())
        if self.rect.width <= 0 or self.rect.height <= 0:
            self.rect = None
            return
        pad = screen.ui_gap(10, minimum=4)
        room = max(1, self.rect.width - 2 * pad - screen.ui_gap(16, minimum=10))
        key = tuple(blocks), font, room
        # Wrapping changes only with content, font or available width. Each frame
        # renders the visible lines, so a long refusal does not redraw every row.
        if key != self.key:
            self.lines = [(line, color) for text, color in blocks for line in self.ui.wrap(text, font, room)]
            self.key = key
        line_h = max(1, font.get_linesize() + screen.ui_gap(3, minimum=1))
        maximum = max(0, len(self.lines) * line_h + 2 * pad - self.rect.height)
        self.offset = max(0.0, min(self.offset, maximum))
        self.bar.sync(self.rect, self.offset, maximum)
        clip = surface.get_clip()
        try:
            surface.set_clip(self.rect)
            first = max(0, int((self.offset - pad) // line_h))
            last = min(len(self.lines), first + self.rect.height // line_h + 3)
            for index in range(first, last):
                line, color = self.lines[index]
                surface.blit(font.render(line, True, color),
                             (self.rect.x + pad, self.rect.y + pad + index * line_h - int(self.offset)))
            self.bar.draw(surface)
        finally:
            surface.set_clip(clip)

    def handle_event(self, screen, event):
        if self.rect is None:
            return False
        pg = self.ui.pygame
        # The native scrollbar handles thumb/track input, but not the wheel.
        # Consume wheel input only over this body; outer page scrolling stays native.
        delta = 0
        if event.type == pg.MOUSEWHEEL:
            delta = getattr(event, "precise_y", event.y)
        elif event.type == pg.MOUSEBUTTONDOWN and event.button in (4, 5):
            delta = 1 if event.button == 4 else -1
        pos = screen.window_to_canvas(event.pos) if hasattr(event, "pos") else screen.get_mouse_canvas_pos()
        if delta and self.bar.visible and pos is not None and self.rect.collidepoint(pos):
            self.offset = self.bar.set_scroll(self.offset - delta * screen.small.get_linesize() * 3)
            return True
        value = self.bar.handle_event(event, screen.window_to_canvas)
        if value is None:
            return False
        self.offset = value
        return True
