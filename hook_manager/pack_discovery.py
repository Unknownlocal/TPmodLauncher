# SPDX-License-Identifier: MPL-2.0
"""Read the Formula pack chosen at startup without loading pack files or code."""

import sys
from types import FunctionType, ModuleType


class PackDiscovery:
    """Keep one startup snapshot for this process, rather than follow menu selections."""

    def __init__(self, game_root):
        # The trusted launcher supplies the installation folder, never a pack-supplied path.
        # Save the path without importing filesystem helpers during an interrupted game import.
        self.game_root = game_root
        self.snapshot = None

    def poll(self):
        """Return pending until startup finishes, then a pack record or an unavailable reason."""
        if self.snapshot is not None:
            return dict(self.snapshot)
        main = sys.modules.get("__main__")
        # Do not call the selection helper: that could read preferences and fill its cache early.
        if (
            type(main) is not ModuleType
            or vars(main).get("_CONSTANTS_LOADED") is not True
        ):
            return {"state": "pending"}
        try:
            self.snapshot = self._read(vars(main))
        except (OSError, RuntimeError, ValueError) as error:
            # Discovery failure must leave an ordinary data pack and the game usable.
            self.snapshot = {"state": "unavailable", "detail": str(error)[:512]}
        return dict(self.snapshot)

    def _read(self, main):
        # The startup marker ensures the game's utils import (including pathlib) has finished.
        from pathlib import Path

        game_root = Path(self.game_root).resolve()
        # These names belong to the supported game's Formula startup path.
        module = sys.modules.get("utils")
        if type(module) is not ModuleType:
            raise ValueError("Formula selection module is unavailable")
        values = vars(module)
        select = values.get("active_data_pack")
        # Confirm which cache key startup used, without executing the game helper.
        if (
            type(select) is not FunctionType
            or main.get("active_data_pack") is not select
        ):
            raise ValueError("Formula selection helper changed")
        defaults = select.__defaults__
        if defaults is None or len(defaults) != 1 or type(defaults[0]) is not str:
            raise ValueError("Formula selection helper defaults changed")
        cache = values.get("_ACTIVE_PACK_CACHE")
        if type(cache) is not dict:
            raise ValueError("Formula selection cache changed")
        name = cache.get(defaults[0])
        # A pack ID is one folder name; reject traversal, controls and Windows drive syntax.
        if (
            type(name) is not str
            or not name
            or len(name) > 128
            or name != name.strip()
            or name == "."
            or ".." in name
            or any(char in name for char in "/\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
        ):
            raise ValueError("Formula startup cache has no supported pack ID")
        data = (game_root / "data").resolve(strict=True)
        folder = (data / name).resolve(strict=True)
        # Links must not turn pack discovery into access outside the installed data tree.
        if (
            not data.is_relative_to(game_root)
            or not folder.is_relative_to(data)
            or folder == data
            or not folder.is_dir()
        ):
            raise ValueError("Formula pack folder leaves the installed data tree")
        return {"state": "ready", "series": "formula", "id": name, "root": str(folder)}

    def stop(self):
        """Forget the pack when this bridge is shut down while its process is still alive."""
        self.snapshot = {"state": "stopped"}
