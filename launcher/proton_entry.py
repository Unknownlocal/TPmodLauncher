# SPDX-License-Identifier: MPL-2.0
"""Enter the Windows launcher after the native wrapper has preserved Steam's Proton prefix."""

import importlib.util
import argparse
import os
from pathlib import Path
import sys
import traceback
import uuid

sys.dont_write_bytecode = True


def _load_helper(root, relative):
    """Load a fixed startup source without exposing game/mod import paths."""
    spec = importlib.util.spec_from_file_location("_proton_" + relative.replace("/", "_"),
                                                  root / "launcher" / (relative + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(game_arguments=None, *, launcher_arguments=(), host_session=None, host_log_path=None):
    root = Path(__file__).resolve().parent.parent
    transport = None
    code = 1
    try:
        if host_session is not None:
            transport = _load_helper(root, "diagnostics/host_channel").HostChannel(root, host_session)
        code = _main(game_arguments, launcher_arguments=launcher_arguments,
                     host_session=host_session, host_log_path=host_log_path, transport=transport)
    except KeyboardInterrupt:
        code = 130
    except Exception:
        # Before entry owns the log, transport failures belong to this fresh
        # session only. Never append them to an earlier launch's launcher.log.
        details = traceback.format_exc()
        if host_session is not None:
            import re
            if re.fullmatch(r"linux-notice\.[A-Za-z0-9]{10}", host_session):
                try:
                    with (root / ".state" / host_session / "transport-error.txt").open("x", encoding="utf-8") as stream:
                        stream.write(details)
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError:
                    pass  # Console output remains an independent attempt.
        print(details, file=sys.stderr, flush=True)
    finally:
        if transport is not None:
            try:
                transport.send("DONE", code)
            except OSError:
                # An unknown native result must never turn a successful run
                # into false success. Preserve any original failure code.
                code = code or 1
            finally:
                transport.close()
    return code


def _main(game_arguments=None, *, launcher_arguments=(), host_session=None, host_log_path=None, transport=None):
    root = Path(__file__).resolve().parent.parent
    presentation = None
    host_lifetime = None
    if host_session is not None:
        channel = _load_helper(root, "diagnostics/linux_failure_channel")
        presentation = channel.LinuxFailureChannel(host_log_path)
    try:
        if host_session is not None:
            lifetime_module = _load_helper(root, "diagnostics/host_lifetime")
            host_lifetime = lifetime_module.HostLifetime(descriptor=transport.reader, detach_stdin=True)
            os.close(transport.reader)
            transport.reader = None
            presentation.host_lifetime = host_lifetime
        entry = _load_helper(root, "entry")
    except Exception as error:
        # Entry's dependency helpers may themselves be missing. Never claim an
        # earlier launch's log as ours; retain the complete startup traceback.
        if presentation is None:
            raise
        details = traceback.format_exc()
        print(details, file=sys.stderr, flush=True)
        presentation(dict(session_id=uuid.uuid4().hex, log_path=str(root / "logs/launcher.log"),
                          log_written=False, failure=dict(reason=str(error), details=details,
                          stage="startup_helpers", level="ERROR",
                          activity=dict(kind="launcher_startup", description="while starting the launcher"))),
                     owned_log=False, wait=True)
        return 1
    # entry.main still validates this exact installed executable. Game
    # arguments cross from the shell wrapper, as separate argv entries. Launcher
    # options have their own prefix so a game's --log-level remains game data.
    arguments = [*launcher_arguments, "--", str(root.parent / "TeamPrincipal.exe"),
                 *(sys.argv[1:] if game_arguments is None else game_arguments)]
    if presentation is None:
        return entry.main(arguments)
    return entry.main(arguments, presentation=presentation, host_lifetime=host_lifetime)


if __name__ == "__main__":
    arguments = sys.argv[1:]
    # The wrapper supplies --. Older direct calls without it remain game argv.
    boundary = arguments.index("--") if "--" in arguments else 0
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host-session")
    parser.add_argument("--host-log-path")
    host, launcher_arguments = parser.parse_known_args(arguments[:boundary])
    raise SystemExit(main(arguments[boundary + 1:] if "--" in arguments else arguments,
                          launcher_arguments=launcher_arguments, host_session=host.host_session,
                          host_log_path=host.host_log_path))
