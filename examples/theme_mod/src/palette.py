# SPDX-License-Identifier: MPL-2.0
"""Fixed colour groups for the custom-theme example's first palette schema.

Each record is (stable number, default RGB/RGBA, native theme roles).
Display records as ``Custom Theme Colour <number>``. Roles are internal metadata.
The numbers are explicit identities for future saved settings, not row indexes.
Never regroup records by their edited colours or renumber existing groups.
"""

PALETTE_SCHEMA = 1
COLOR_LABEL_PREFIX = "Custom Theme Colour"

# Grouped by identical defaults in UITheme from the installed game inspected on
# 2026-10-16. Initial numbering follows each colour's first declaration in UITheme.
# This is a fixed mapping: normal mod loading must not regenerate it from the game.
# OVERLAY includes alpha; all other defaults are RGB.
COLOR_GROUPS = (
    (1, (5, 10, 19), ("BACKGROUND",)),
    (2, (7, 14, 26), ("HEADER",)),
    (3, (10, 18, 31), ("PANEL",)),
    (4, (14, 24, 39), ("PANEL_ALT",)),
    (5, (19, 35, 47), ("PANEL_LIGHT",)),
    (6, (16, 26, 42), ("CARD",)),
    (7, (20, 32, 49), ("CARD_ALT",)),
    (8, (20, 33, 53), ("CARD_HOVER",)),
    (9, (43, 61, 83), ("BORDER",)),
    (10, (77, 103, 138), ("BORDER_HOVER",)),
    (11, (48, 112, 185), ("PRIMARY", "SELECTION_BORDER")),
    (12, (62, 135, 216), ("PRIMARY_HOVER",)),
    (13, (17, 49, 82), ("PRIMARY_DARK",)),
    (14, (29, 64, 92), ("PRIMARY_SOFT",)),
    (15, (18, 73, 112), ("NAV_PRIMARY",)),
    (16, (23, 96, 148), ("NAV_PRIMARY_HOVER",)),
    (17, (36, 78, 111), ("NAV_PRIMARY_PRESSED",)),
    (18, (240, 240, 240), ("TEXT", "SELECTION_TEXT")),
    (19, (157, 171, 190), ("TEXT_MUTED",)),
    (20, (112, 126, 147), ("TEXT_DIM", "DISABLED_TEXT")),
    (21, (70, 224, 126), ("SUCCESS",)),
    (22, (84, 235, 140), ("SUCCESS_HOVER",)),
    (23, (16, 72, 48), ("SUCCESS_DARK",)),
    (24, (242, 194, 76), ("WARNING",)),
    (25, (112, 82, 20), ("WARNING_DARK",)),
    (26, (222, 80, 82), ("DANGER",)),
    (27, (235, 96, 98), ("DANGER_HOVER",)),
    (28, (140, 58, 58), ("DANGER_DARK",)),
    (29, (58, 27, 34), ("NOTICE_FILL",)),
    (30, (236, 91, 99), ("NOTICE_BORDER",)),
    (31, (255, 159, 164), ("NOTICE_TEXT",)),
    (32, (44, 52, 64), ("DISABLED",)),
    (33, (58, 70, 88), ("NEUTRAL_BUTTON",)),
    (34, (72, 87, 108), ("NEUTRAL_BUTTON_HOVER",)),
    (35, (32, 43, 58), ("BAR_BACKGROUND",)),
    (36, (92, 173, 249), ("TYRE_CORNERING",)),
    (37, (179, 143, 247), ("TYRE_TRACTION",)),
    (38, (70, 214, 170), ("TYRE_DURABILITY",)),
    (39, (250, 179, 87), ("TYRE_TEMPERATURE",)),
    (40, (66, 70, 80), ("SELECTION_FILL",)),
    (41, (78, 83, 95), ("SELECTION_FILL_HOVER",)),
    (42, (10, 16, 30), ("MARKET_BACKGROUND",)),
    (43, (16, 25, 43), ("MARKET_CARD", "TABLE_ROW")),
    (44, (19, 29, 49), ("MARKET_CARD_ALT", "TABLE_ROW_ALT")),
    (45, (23, 35, 58), ("MARKET_HEADER", "TABLE_HEADER")),
    (46, (40, 58, 90), ("MARKET_BORDER", "TABLE_GRID")),
    (47, (20, 48, 32), ("RATING_GREEN_LOW",)),
    (48, (31, 89, 49), ("RATING_GREEN_MID",)),
    (49, (48, 142, 72), ("RATING_GREEN_HIGH",)),
    (50, (178, 55, 57), ("MARKET_RATING_RED",)),
    (51, (208, 168, 52), ("MARKET_RATING_YELLOW",)),
    (52, (0, 0, 0, 160), ("OVERLAY",)),
)
