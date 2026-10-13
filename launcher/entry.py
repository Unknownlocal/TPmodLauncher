# SPDX-License-Identifier: MPL-2.0
"""Check bundled dependencies or launch Steam's supplied game command.

Load helpers from this script's folder because the included Python is configured
not to search script folders automatically."""

import argparse
import importlib.util
import math
from pathlib import Path
import sys
import tomllib

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
        "--backend",
        choices=("python", "native"),
        default="python",
        help="Frida provider to use (default: the existing Python binding)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="check bundled Frida without starting anything",
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
    native = None
    try:
        if arguments.backend == "native":
            # Both launch paths require the isolated bundled interpreter, even without a wheel.
            settings = tomllib.loads(
                _dependencies.local_path(root, "pyproject.toml").read_text(
                    encoding="utf-8"
                )
            )
            _dependencies.require_embedded_runtime(
                root, {"runtime": settings["tool"]["modlauncher"]["runtime"]}
            )
            source = _dependencies.local_path(root, "launcher/native_worker.py")
            spec = importlib.util.spec_from_file_location("_tpm_native_worker", source)
            native = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(native)
            # Verify and start the worker without importing the large Frida Python package.
            frida = native.Backend(native.verify_worker(root))
        else:
            frida = _dependencies.import_frida(root)
    except Exception as error:
        print(f"Dependency verification failed: {error}", file=sys.stderr)
        return 1
    if arguments.check_only:
        try:
            if native is not None:
                frida.close()
        except Exception as error:
            print(f"Worker shutdown failed: {error}", file=sys.stderr)
            return 1
        print(f"Bundled dependencies ready: Frida {frida.__version__}")
        return 0
    result = 1
    try:
        # The launcher folder and game executable share the same installation folder.
        expected = root.parent / "TeamPrincipal.exe"
        source = _dependencies.local_path(root, "launcher/process_lifecycle.py")
        spec = importlib.util.spec_from_file_location("_tpm_process_lifecycle", source)
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        command = lifecycle.validate_command(command, expected)
        source = _dependencies.local_path(root, "launcher/game_bridge.py")
        spec = importlib.util.spec_from_file_location("_tpm_game_bridge", source)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        bridge_factory = bridge.factory(root, frida)
        result = lifecycle.run_launcher(
            frida,
            command,
            _dependencies.local_path(root, ".state/controller.lock"),
            _dependencies.local_path(root, "logs/launcher.log"),
            restart_grace=arguments.restart_grace,
            bridge_factory=bridge_factory,
        )
    # Frida setup errors must produce a failed launch, even before the controller loop starts.
    except Exception as error:
        print(f"Launcher failed: {error}", file=sys.stderr)
    finally:
        # Controller cleanup restores the manager first; then release the worker itself.
        if native is not None:
            try:
                frida.close()
            except Exception as error:
                print(f"Worker shutdown failed: {error}", file=sys.stderr)
                result = 1
    return result


# Run only when this file is started directly; return its success or error code.
if __name__ == "__main__":
    raise SystemExit(main())
