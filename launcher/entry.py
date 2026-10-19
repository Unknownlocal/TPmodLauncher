# SPDX-License-Identifier: MPL-2.0
"""Check bundled dependencies or launch Steam's supplied game command.

Load helpers from this script's folder because the included Python is configured
not to search script folders automatically."""

import argparse
import importlib.util
from importlib.metadata import version
import math
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
    # Arguments before -- configure the launcher; arguments after it belong to the game.
    parser = argparse.ArgumentParser(
        description="Check dependencies or launch and track the game."
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="check bundled dependencies and the Python runner without starting the game",
    )
    parser.add_argument(
        "--restart-grace",
        type=float,
        default=2.0,
        help="seconds to wait for a game replacement after the last process exits (default: 2)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Steam's original command, supplied after --",
    )
    arguments = parser.parse_args(argv)
    if not math.isfinite(arguments.restart_grace) or arguments.restart_grace <= 0:
        parser.error("--restart-grace must be a finite number greater than zero")
    command = arguments.command
    if command and command[0] == "--":
        command = command[1:]
    # Do not accidentally treat a verification request as permission to start the game.
    if arguments.check_only and command:
        parser.error("--check-only cannot be combined with a game command")
    if not arguments.check_only and not command:
        parser.error(
            "supply Steam's original game command after --, or use --check-only"
        )
    root = Path(__file__).resolve().parent.parent
    try:
        frida = _dependencies.import_dependencies(root)
    except _dependencies.DependencyError as error:
        print(f"Dependency verification failed: {error}", file=sys.stderr)
        return 1
    try:
        # Source locations are trusted only after the dependency setup is verified.
        _dependencies.load_source_packages(root)
        from mod_runtime import ABI_VERSION, RunnerBundle
        from mod_runtime.bundle import DIRECTORY

        runner_bundle = RunnerBundle(root / DIRECTORY)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(
            f"Python runner verification failed: {error}. Restore the complete launcher release.",
            file=sys.stderr,
        )
        return 1
    if arguments.check_only:
        print(
            f"Bundled dependencies ready: Frida {version('frida')}; Wasmtime {version('wasmtime')}; Python runner {ABI_VERSION}"
        )
        return 0
    try:
        # Only the launcher's own packages gain import paths; data-pack Python is never imported.
        from launcher import game_bridge as bridge, process_lifecycle as lifecycle
        from launcher.pack_mods import PackMods
        from launcher.compilation_cache import CompilationCache

        # The launcher folder and game executable share the same installation folder.
        expected = root.parent / "TeamPrincipal.exe"
        command = lifecycle.validate_command(command, expected)
        bridge_factory = bridge.factory(root, frida)
        cache = CompilationCache(root)
        return lifecycle.run_launcher(
            frida,
            command,
            _dependencies.local_path(root, ".state/controller.lock"),
            _dependencies.local_path(root, "logs/launcher.log"),
            restart_grace=arguments.restart_grace,
            bridge_factory=bridge_factory,
            mods_factory=lambda report, bridge: PackMods(
                root.parent,
                report,
                set_menu=bridge.set_menu,
                set_settings=bridge.set_settings,
                set_themes=bridge.set_themes,
                set_loading=bridge.set_loading,
                background=True,
                cache=cache,
                runner_bundle=runner_bundle,
                wake=bridge.wake,
            ),
        )
    # Frida setup errors must produce a failed launch, even before the controller loop starts.
    except Exception as error:
        print(f"Launcher failed: {error}", file=sys.stderr)
        return 1


# Run only when this file is started directly; return its success or error code.
if __name__ == "__main__":
    raise SystemExit(main())
