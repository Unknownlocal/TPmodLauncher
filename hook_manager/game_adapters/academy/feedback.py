# SPDX-License-Identifier: MPL-2.0
"""Draw a short academy refusal using the screen's existing font/text fitting."""


def draw_refusal(screen, surface, message):
    width = surface.get_width()
    text = screen.fit_text("Regulation: " + message, screen.small, max(1, width - 24))
    rendered = screen.small.render(text, True, (255, 230, 210))
    height = rendered.get_height()
    y = max(0, surface.get_height() - height - 12)
    surface.fill((32, 32, 32), (0, max(0, y - 6), width, height + 12))
    surface.blit(rendered, (12, y))
