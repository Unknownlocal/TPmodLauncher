# SPDX-License-Identifier: MPL-2.0
"""Enter the Windows launcher after the native wrapper has preserved Steam's Proton prefix."""

import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def main(game_arguments=None):
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("_proton_launcher_entry", root / "launcher/entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    # entry.main still validates this exact installed executable. Only game
    # arguments cross from the shell wrapper, as separate argv entries.
    return entry.main(["--", str(root.parent / "TeamPrincipal.exe"),
                       *(sys.argv[1:] if game_arguments is None else game_arguments)])


if __name__ == "__main__":
    raise SystemExit(main())
