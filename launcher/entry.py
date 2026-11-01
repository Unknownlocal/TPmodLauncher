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


def _load_helper(relative):
    """Load fixed standard-library startup helpers without changing import paths."""
    source = Path(__file__).resolve().parent / (relative + ".py")
    spec = importlib.util.spec_from_file_location("_tpm_" + relative.replace("/", "_"), source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_dependencies = _load_helper("bundled_dependencies")
_diagnostics = _load_helper("diagnostics/core")


def main(argv=None, *, outcome=None) -> int:
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
    if outcome is None:
        outcome = {}
    outcome.update(_diagnostics.build_launch_outcome(root / "logs/launcher.log"))
    try:
        lock_path = _dependencies.local_path(root, ".state/controller.lock")
        log_path = _dependencies.local_path(root, "logs/launcher.log")
        # Claim the log before verification can fail, and retain ownership until
        # all controller/game cleanup and final filesystem synchronisation finish.
        with _diagnostics.log_ownership(lock_path), _diagnostics.reporter(log_path, outcome=outcome) as logger:
            try:
                code = _run(root, arguments, command, logger, lock_path, log_path)
            except KeyboardInterrupt:
                logger.info("INTERRUPTED during launcher setup")
                code = 130
            except Exception as error:
                _diagnostics.handle_error(logger, error, message="Launcher failed",
                                          stage="launcher_setup", component="startup")
                code = 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        # No owned log is available here (for example, a rejected duplicate).
        # Do not append to another launch's file or describe it as our own log.
        _diagnostics.handle_error(lambda text: print(text, file=sys.stderr, flush=True), error,
                                  message="Launcher failed", stage="log_ownership", outcome=outcome)
        return 1
    return 1 if outcome["log_error"] is not None else code


def _run(root, arguments, command, logger, lock_path, log_path):
    """Verify and run within the already-owned log; presentation is independent."""
    try:
        frida = _dependencies.import_dependencies(root)
    except Exception as error:
        _diagnostics.handle_error(logger, error, message="Dependency verification failed",
                                  stage="dependency_verification", component="startup")
        return 1
    try:
        # Source locations are trusted only after the dependency setup is verified.
        _dependencies.load_source_packages(root)
        from mod_runtime import ABI_VERSION, RunnerBundle
        from mod_runtime.bundle import DIRECTORY

        runner_bundle = RunnerBundle(root / DIRECTORY)
    except Exception as error:
        _diagnostics.handle_error(logger, error, message="Python runner verification failed",
                                  stage="runner_verification", component="startup")
        logger.error("Restore the complete launcher release.")
        return 1
    if arguments.check_only:
        print(
            f"Bundled dependencies ready: Frida {version('frida')}; Wasmtime {version('wasmtime')}; Python runner {ABI_VERSION}"
        )
        return 0
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
        lock_path,
        log_path,
        restart_grace=arguments.restart_grace,
        bridge_factory=bridge_factory,
        logger=logger,
        mods_factory=lambda submit_log, bridge: PackMods(
            root.parent,
            submit_log,
            set_menu=bridge.set_menu,
            set_settings=bridge.set_settings,
            set_themes=bridge.set_themes,
            set_loading=bridge.set_loading,
            background=True,
            cache=cache,
            runner_bundle=runner_bundle,
            wake=bridge.wake,
            bridge=bridge,
        ),
    )


# Run only when this file is started directly; return its success or error code.
if __name__ == "__main__":
    raise SystemExit(main())
