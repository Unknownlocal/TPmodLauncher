# SPDX-License-Identifier: MPL-2.0
"""Print Steam launch options for this installed bundle without changing Steam."""

import importlib.util
from pathlib import Path, PureWindowsPath
import sys

sys.dont_write_bytecode = True
WINDOWS_HOST = sys.platform == "win32"
_spec = importlib.util.spec_from_file_location(
    "_launch_dependencies", Path(__file__).with_name("bundled_dependencies.py")
)
dependencies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dependencies)


def launch_line(root, manifest):
    """Keep Python absolute and the script relative to shorten Steam's launch line."""
    runtime = manifest["runtime"]
    interpreter = root / runtime["directory"] / "python.exe"
    return f'"{PureWindowsPath(interpreter)}" -B "modLauncher\\launcher\\entry.py" -- %command%'


def main():
    root = Path(__file__).resolve().parent.parent
    try:
        manifest = dependencies.read_manifest(root)
        if not WINDOWS_HOST:
            raise ValueError("Run SteamLaunchOptions.cmd on Windows.")
        game = root.parent / "TeamPrincipal.exe"
        if root.name != "modLauncher" or not game.is_file():
            raise ValueError(f"Extract modLauncher directly beside {game.name}, then run this helper again.")
        if not dependencies.local_path(root, manifest["runtime"]["directory"] + "/python.exe").is_file():
            raise ValueError("The bundled Python is missing. Extract the complete release again.")
        print("Copy the entire line below into Steam > Properties > General > Launch Options:\n")
        print(launch_line(root, manifest))
        print("\nKeep %command% unchanged. This helper does not change Steam settings or start the game.")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Cannot generate launch options: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
