# SPDX-License-Identifier: MPL-2.0
"""Display and copy Steam launch options without changing Steam settings."""

import base64
import importlib.util
import os
from pathlib import Path, PureWindowsPath
import subprocess
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


def copy_to_clipboard(line):
    """Copy exactly this Unicode string using Windows' built-in clipboard command."""
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise OSError("Windows system directory is unavailable")
    powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    # Treat the path and %command% as data, including quotes, $, and non-ASCII
    # characters. No part of the user's launch line becomes PowerShell code.
    payload = base64.b64encode(line.encode("utf-16le")).decode("ascii")
    command = (
        "$ErrorActionPreference = 'Stop'; "
        "Set-Clipboard -Value ([Text.Encoding]::Unicode.GetString("
        f"[Convert]::FromBase64String('{payload}')))"
    )
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
        check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


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
        line = launch_line(root, manifest)
        print("Steam > Properties > General > Launch Options:\n")
        print(line)
        try:
            copy_to_clipboard(line)
        except (OSError, subprocess.SubprocessError):
            print("\nCould not copy to the clipboard. Copy the displayed line manually.")
        else:
            print("\nCopied to clipboard. Press Ctrl+V in Steam's Launch Options field.")
        print("\nKeep %command% unchanged. This helper does not change Steam settings or start the game.")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Cannot generate launch options: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
