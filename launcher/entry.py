# SPDX-License-Identifier: MPL-2.0
"""Check the included Python and Frida from the command line.

Load the dependency helper from this script's folder because the included Python
is configured not to search script folders automatically."""

import argparse
import importlib.util
from pathlib import Path
import sys

# Do not create compiled Python files when loading our code or packages.
sys.dont_write_bytecode = True
# Find the helper beside this script; the included Python does not search here automatically.
_source = Path(__file__).resolve().with_name("bundled_dependencies.py")
_spec = importlib.util.spec_from_file_location("_tpm_bundled_dependencies", _source)
_dependencies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dependencies)


def main(argv=None) -> int:
    # argv can supply command-line arguments; None reads them normally; return 0 on success.
    # Require --check-only while this script only checks packages and cannot start the game.
    parser = argparse.ArgumentParser(description="Verify bundled launcher dependencies offline.")
    parser.add_argument("--check-only", action="store_true", required=True,
                        help="verify bundled packages and check Frida imports; do not start the game")
    parser.parse_args(argv)
    # Find the launcher folder beside this script, regardless of Steam's working folder.
    root = Path(__file__).resolve().parent.parent
    try:
        frida = _dependencies.import_frida(root)
    # Report problems with Python or the included packages and stop.
    except _dependencies.DependencyError as error:
        print(f"Dependency verification failed: {error}", file=sys.stderr)
        return 1
    print(f"Bundled dependencies ready: Frida {frida.__version__}")
    return 0


# Run only when this file is started directly; return its success or error code.
if __name__ == "__main__":
    raise SystemExit(main())
