# SPDX-License-Identifier: MPL-2.0
"""Independent native theme interface; default colours come from the example baseline."""

from pathlib import Path
import runpy
from types import ModuleType


def modules(palette_path, settings_path):
    groups = runpy.run_path(str(palette_path))["COLOR_GROUPS"]
    theme = ModuleType("ui.theme")
    theme.UITheme = type("UITheme", (), {"__module__": theme.__name__, **{
        role: tuple(default) for _, default, roles in groups for role in roles
    }})
    packs = ModuleType("ui.color_packs")
    packs.COLOR_PACK_LABELS = {"blue": "Default Blue", "emerald": "Emerald"}
    packs.COLOR_PACKS = tuple(packs.COLOR_PACK_LABELS)
    packs.ACTIVE_COLOR_PACK = "blue"
    packs.graphics_settings_path = lambda: Path(settings_path)
    class ChromePalette(dict):
        def __getitem__(self, colour):
            return tuple(colour)
    packs.ChromePalette = ChromePalette
    packs.CHROME, packs.SESSION_CHROME = ChromePalette(), ChromePalette()
    return theme, packs
