# SPDX-License-Identifier: MPL-2.0
"""Check native Linux helper output and Proton argv rewriting with owned processes."""

import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys

import pytest

from tests.launcher.fixtures.failure_support import wait_for

from launcher import proton_entry
from launcher import package_release as packaging

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture
def installed(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Native Bash tests run on Linux/WSL")
    root = tmp_path / "Game's café $HOME ; owned/modLauncher"
    (root / "launcher").mkdir(parents=True)
    (root / "libs/python-fixture").mkdir(parents=True)
    (root / "libs/python-fixture/python.exe").write_bytes(b"inert Windows runtime fixture")
    (root.parent / "TeamPrincipal.exe").write_bytes(b"inert game fixture")
    (root / "bundled_packages.toml").write_text(
        '[runtime]\nversion = "3.13.16"\ndirectory = "libs/python-fixture"\n[packages]\n'
    )
    for name in ("steam_linux_common.sh", "steam_launch_options.sh", "launch_proton.sh"):
        shutil.copyfile(PROJECT / "launcher" / name, root / "launcher" / name)
    (root / "SteamLaunchOptions.sh").write_bytes(packaging.linux_helper_bytes())
    # Restrict PATH to owned fixtures and required utilities. Clipboard tests must
    # never accidentally reach a real desktop's clipboard programs.
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname", "realpath", "cat", "mkdir", "mktemp", "rmdir", "mkfifo", "rm"):
        (tools / name).symlink_to(shutil.which(name))
    environment = os.environ.copy()
    environment.update(PATH=str(tools), DISPLAY="", WAYLAND_DISPLAY="")
    return root, tools, environment


def run_helper(installed):
    root, _, environment = installed
    return subprocess.run(["/bin/bash", str(root / "SteamLaunchOptions.sh")],
                          env=environment, capture_output=True, text=True, timeout=10)


def test_linux_line_is_always_visible_and_shell_quoted_without_python(installed):
    root, _, _ = installed
    result = run_helper(installed)
    assert result.returncode == 0, result.stderr
    line = next(row for row in result.stdout.splitlines() if row.startswith("bash "))
    assert shlex.split(line) == ["bash", str(root / "launcher/launch_proton.sh"), "%command%"]
    assert "Could not copy to the clipboard" in result.stdout
    assert "experimental and untested" in result.stdout


@pytest.mark.parametrize("backend", ["wl-copy", "xclip", "xsel"])
def test_linux_clipboard_copies_exact_displayed_line(installed, backend):
    root, tools, environment = installed
    capture = root.parent / "clipboard-fixture.txt"
    command = tools / backend
    command.write_text('#!/bin/bash\ncat > "$CLIPBOARD_CAPTURE"\n')
    command.chmod(0o755)
    environment.update(CLIPBOARD_CAPTURE=str(capture))
    environment["WAYLAND_DISPLAY" if backend == "wl-copy" else "DISPLAY"] = "owned-fixture"
    result = run_helper(installed)
    assert result.returncode == 0, result.stderr
    line = next(row for row in result.stdout.splitlines() if row.startswith("bash "))
    assert capture.read_text() == line
    assert "Copied to clipboard" in result.stdout


def test_linux_clipboard_failure_keeps_line_displayed(installed):
    _, tools, environment = installed
    command = tools / "wl-copy"
    command.write_text("#!/bin/bash\nexit 1\n")
    command.chmod(0o755)
    environment["WAYLAND_DISPLAY"] = "owned-fixture"
    result = run_helper(installed)
    assert result.returncode == 0
    assert "bash '" in result.stdout and "Copy the displayed line manually" in result.stdout


@pytest.mark.parametrize("log_level", [None, "debug", "4"])
def test_proton_wrapper_keeps_prefix_environment_arguments_and_exit_status(installed, log_level):
    root, tools, environment = installed
    proton = tools / "proton"
    capture = root.parent / "proton-argv.json"
    proton.write_text(
        f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
        "Path(os.environ['ARGV_CAPTURE']).write_text(json.dumps({'args': sys.argv[1:], "
        "'prefix': os.environ['STEAM_COMPAT_DATA_PATH']}))\nraise SystemExit(23)\n"
    )
    proton.chmod(0o755)
    outer = tools / "runtime-wrapper"
    outer.write_text(f'#!{sys.executable}\nimport os,sys\nassert sys.argv[1:3] == ["--marker", "space value"]\nos.execv(sys.argv[3], sys.argv[3:])\n')
    outer.chmod(0o755)
    environment.update(ARGV_CAPTURE=str(capture), STEAM_COMPAT_DATA_PATH="owned prefix with spaces")
    game_args = ["--save", "quotes ' $value ; `literal`", "", "--log-level", "game-owned"]
    options = ["--log-level", log_level] if log_level else []
    result = subprocess.run([
        "/bin/bash", str(root / "launcher/launch_proton.sh"), *options,
        str(outer), "--marker", "space value", str(proton), "waitforexitandrun",
        str(root.parent / "TeamPrincipal.exe"), *game_args,
    ], env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 23, result.stderr
    record = json.loads(capture.read_text())
    assert record["prefix"] == "owned prefix with spaces"
    assert record["args"][:4] == ["waitforexitandrun", str(root / "libs/python-fixture/python.exe"), "-B", "-c"]
    assert "proton_entry.py" in record["args"][4]
    prefix = ["--log-level", log_level] if log_level else []
    session = record["args"][6]
    host_prefix = ["--host-session", session, "--host-log-path", str(root / "logs/launcher.log")]
    assert record["args"][5:] == [*host_prefix, *prefix, "--", *game_args]
    # Execute the exact captured bootstrap against an owned entry stub. Stand in
    # for Wine's mapped sys.executable; no fixed drive or host path is required.
    shutil.copyfile(PROJECT / "launcher/proton_entry.py", root / "launcher/proton_entry.py")
    (root / "launcher/diagnostics").mkdir()
    shutil.copyfile(PROJECT / "launcher/diagnostics/linux_failure_channel.py", root / "launcher/diagnostics/linux_failure_channel.py")
    shutil.copyfile(PROJECT / "launcher/diagnostics/host_lifetime.py", root / "launcher/diagnostics/host_lifetime.py")
    (root / ".state" / session).mkdir()
    (root / "launcher/entry.py").write_text(
        "import json\nfrom pathlib import Path\ndef main(args, *, presentation, host_lifetime):\n"
        "    assert presentation.host_log_path.endswith('/logs/launcher.log')\n"
        "    Path(__file__).with_name('received.json').write_text(json.dumps(args))\n    return 31\n"
    )
    bootstrap_result = subprocess.run([
        sys.executable, "-B", "-c", "import sys;sys.executable=sys.argv.pop(1);" + record["args"][4],
        str(root / "libs/python-fixture/python.exe"), *record["args"][5:],
    ], env=environment, capture_output=True, text=True, timeout=10)
    assert bootstrap_result.returncode == 31, bootstrap_result.stderr
    assert json.loads((root / "launcher/received.json").read_text()) == [
        *prefix, "--", str(root.parent / "TeamPrincipal.exe"), *game_args,
    ]


def test_wrapper_rejects_unrelated_game_target(installed):
    root, _, environment = installed
    other = root.parent / "Unrelated.exe"
    other.write_bytes(b"owned unrelated fixture")
    result = subprocess.run([
        "/bin/bash", str(root / "launcher/launch_proton.sh"), "/unused/proton",
        "waitforexitandrun", str(other),
    ], env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and "installed game target was not found" in result.stderr


def test_invalid_runtime_path_is_not_evaluated(installed):
    root, _, _ = installed
    (root / "bundled_packages.toml").write_text('[runtime]\ndirectory = "libs/../../outside"\n')
    result = run_helper(installed)
    assert result.returncode == 1 and "manifest path is invalid" in result.stderr


def test_windows_proton_adapter_supplies_only_installed_game_and_original_arguments(tmp_path, monkeypatch):
    root = tmp_path / "game/modLauncher"
    (root / "launcher").mkdir(parents=True)
    (root / "launcher/entry.py").write_text(
        "import json\nfrom pathlib import Path\ndef main(args):\n"
        "    Path(__file__).with_name('received.json').write_text(json.dumps(args))\n    return 31\n"
    )
    monkeypatch.setattr(proton_entry, "__file__", str(root / "launcher/proton_entry.py"))
    arguments = ["--save", "space ' ; literal", ""]
    assert proton_entry.main(arguments) == 31
    assert json.loads((root / "launcher/received.json").read_text()) == [
        "--", str(root.parent / "TeamPrincipal.exe"), *arguments,
    ]


@pytest.fixture
def supervised_notice(tmp_path):
    """Run native shell control flow with inert desktop tools, including on MSYS.

    Git Bash exercises shell/loopback behaviour on Windows, not a Linux desktop
    or Wine. The real Zenity, terminal and editor are never invoked here.
    """
    bash = shutil.which("bash")
    if bash is None and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(candidate) if candidate.is_file() else None
    if bash is None:
        pytest.skip("Bash is unavailable")
    root = tmp_path / "Game's $HOME ; literal/modLauncher"
    folder = root / "launcher/diagnostics"
    folder.mkdir(parents=True)
    for name in ("launch_proton.sh", "steam_linux_common.sh", "proton_entry.py", "entry.py", "bundled_dependencies.py"):
        shutil.copyfile(PROJECT / "launcher" / name, folder.parent / name)
    for source in (PROJECT / "launcher/diagnostics").iterdir():
        if source.suffix in (".py", ".sh"):
            shutil.copyfile(source, folder / source.name)
    (root / "hook_manager").mkdir()
    shutil.copyfile(PROJECT / "hook_manager/diagnostics.py", root / "hook_manager/diagnostics.py")
    # Run the actual entry's ownership, final logging and presentation route;
    # replace only game startup with an owned error in this disposable copy.
    with (root / "launcher/entry.py").open("a", encoding="utf-8") as stream:
        stream.write('\ndef _run(root, arguments, command, logger, lock_path, log_path, *, host_lifetime=None):\n'
                     '    import os\n'
                     '    if os.environ["LINUX_PROBE_MODE"] == "ongoing":\n'
                     '        (root / "ongoing").touch()\n'
                     '        assert host_lifetime.lost.wait(8)\n'
                     '        host_lifetime.check()\n'
                     '    with _diagnostics.activity_scope(logger, "saving"):\n'
                     '        _diagnostics.handle_error(logger, RuntimeError("literal <b> $HOME ; `touch unwanted`\\nCOMPLETE ORIGINAL ERROR " + "x" * 7000 + " END"))\n'
                     '    return 37\n')
    (root / "libs/python-fixture").mkdir(parents=True)
    (root / "libs/python-fixture/python.exe").write_bytes(b"inert fixture")
    (root.parent / "TeamPrincipal.exe").write_bytes(b"inert fixture")
    (root / "bundled_packages.toml").write_text('[runtime]\ndirectory = "libs/python-fixture"\n')
    tools = tmp_path / "owned-tools"
    tools.mkdir()
    scripts = {
        # cat also bridges MSYS FIFOs to a Windows-readable anonymous stdin
        # pipe. The controller is a child of this shim, never its exec target.
        "proton": 'exec 8<&0\ncoproc RELAY { cat <&8 8>&-; }\nrelay_pid=$RELAY_PID\n'
                  'exec 9<&"${RELAY[0]}"\n'
                  '"$LINUX_PROBE_PYTHON" "$LINUX_PROBE_CONTROLLER" "$@" <&9 8>&-\n'
                  'code=$?\nkill -TERM "$relay_pid" 2>/dev/null || true\nwait "$relay_pid" 2>/dev/null || true\nexit "$code"',
        "xterm": 'printf "%s" "$$" > "$LINUX_PROBE_ROOT/terminal-pid"\n'
                 'printf "%s\\0" "$@" > "$LINUX_PROBE_ROOT/terminal-args"\n'
                 'while [[ $1 != -e ]]; do shift; done\nshift\nexec "$@"',
        "zenity": 'printf "%s\\0" "$@" > "$LINUX_PROBE_ROOT/dialog-args"\n'
                  'if [[ $LINUX_PROBE_MODE == failure ]]; then echo "fixture display failure" >&2; exit 1; fi\n'
                  'if [[ $LINUX_PROBE_MODE == console ]]; then\n'
                  '  printf "%s" "$$" > "$LINUX_PROBE_ROOT/dialog-pid"\n'
                  '  mkfifo "$LINUX_PROBE_ROOT/dialog-lifetime"\n'
                  '  read -r unused < "$LINUX_PROBE_ROOT/dialog-lifetime"\nfi\n'
                  'echo "fixture harmless GTK warning" >&2\nexit "$ZENITY_ESC"',
        "xdg-open": 'printf "%s" "$1" > "$LINUX_PROBE_ROOT/editor-path"',
    }
    for name, text in scripts.items():
        target = tools / name
        target.write_text("#!/usr/bin/env bash\n" + text + "\n", encoding="utf-8", newline="\n")
        target.chmod(0o755)
    environment = dict(os.environ, LINUX_PROBE_ROOT=root.as_posix(), LINUX_PROBE_PYTHON=Path(sys._base_executable).as_posix(),
                       LINUX_PROBE_CONTROLLER=(PROJECT / "tests/launcher/fixtures/linux_controller_probe.py").as_posix(),
                       STEAM_COMPAT_DATA_PATH="owned prefix with spaces", MSYS2_ARG_CONV_EXCL="*")
    children = []

    def start(mode):
        if mode == "hung-terminal":
            (tools / "xterm").write_text('#!/usr/bin/env bash\nprintf "%s" "$$" > "$LINUX_PROBE_ROOT/terminal-pid"\n'
                                        'mkfifo "$LINUX_PROBE_ROOT/terminal-hang"\nread -r unused < "$LINUX_PROBE_ROOT/terminal-hang"\n', newline="\n")
        wrapper = root / "launcher/launch_proton.sh"
        wrapper.write_text(wrapper.read_text().replace('set -euo pipefail',
            'set -euo pipefail\nprintf "%s" "$$" > "$LINUX_PROBE_ROOT/wrapper-pid"'), newline="\n")
        if mode == "entry-failure":
            (root / "launcher/entry.py").write_text('raise ImportError("owned early entry failure")\n')
        environment.update(LINUX_PROBE_MODE=mode, DISPLAY="" if mode == "missing" else ":owned-fixture", WAYLAND_DISPLAY="")
        output = (root / "console.txt").open("w", encoding="utf-8")
        # Only the fixed script is shell code. Every path, option and game
        # argument crosses argv, including literal shell-looking characters.
        process = subprocess.Popen([
            bash, "--noprofile", "--norc", "-c",
            'export PATH="$(cd -- "$1" && pwd -P):$PATH"; shift\n'
            'arguments=()\nfor argument; do\n'
            '  if [[ -f $argument ]]; then argument="$(cd -- "$(dirname -- "$argument")" && pwd -P)/${argument##*/}"; fi\n'
            '  arguments+=("$argument")\ndone\nexec bash "${arguments[@]}"',
            "owned-probe", tools.as_posix(), (root / "launcher/launch_proton.sh").as_posix(),
            "--log-level", "debug", (tools / "proton").as_posix(), "waitforexitandrun",
            (root.parent / "TeamPrincipal.exe").as_posix(), "--save", "quotes ' $value ; `literal`", "",
        ], env=environment, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        children.append((process, output))
        return process, root

    yield start
    for process, output in children:
        if process.poll() is None:
            wrapper_pid = root / "wrapper-pid"
            if wrapper_pid.exists():
                subprocess.run([bash, "-c", 'kill -TERM -- "$1"', "owned", wrapper_pid.read_text()], capture_output=True, timeout=3)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        output.close()


def test_host_dialog_dismissal_preserves_proton_contract_and_native_paths(supervised_notice):
    process, root = supervised_notice("dialog")
    assert process.wait(timeout=10) == 37, (root / "console.txt").read_text()
    capture = json.loads((root / "controller.json").read_text())
    args = capture["args"]
    assert capture["prefix"] == "owned prefix with spaces"
    boundary = args.index("--")
    assert args[boundary + 1:] == ["--save", "quotes ' $value ; `literal`", ""]
    assert args[boundary - 2:boundary] == ["--log-level", "debug"]
    wait_for(lambda: (root / "editor-path").exists())
    host_log = args[args.index("--host-log-path") + 1]
    assert (root / "editor-path").read_text() == host_log
    dialog = (root / "dialog-args").read_bytes().decode().split("\0")
    assert "--no-markup" in dialog and any("literal <b> $HOME ; `touch unwanted`" in argument for argument in dialog)
    assert not (root / "unwanted").exists()
    assert "COMPLETE ORIGINAL ERROR" in (root / "logs/launcher.log").read_text()
    assert (root / "terminal-args").exists()


def test_closing_native_console_closes_only_its_dialog_and_preserves_failure_code(supervised_notice):
    process, root = supervised_notice("console")
    wait_for(lambda: (root / "dialog-pid").exists())
    terminal = (root / "terminal-pid").read_text()
    dialog = (root / "dialog-pid").read_text()
    bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
    result = subprocess.run([bash, "-c", 'kill -TERM -- "$1"', "owned", terminal], capture_output=True, timeout=3)
    assert result.returncode == 0, result.stderr
    assert process.wait(timeout=8) == 37
    result = subprocess.run([bash, "-c", 'kill -0 -- "$1"', "owned", dialog], capture_output=True, timeout=3)
    assert result.returncode != 0, "The owned dialog outlived its console"


def test_early_proton_entry_failure_keeps_full_details_without_claiming_old_log(supervised_notice):
    process, root = supervised_notice("entry-failure")
    assert process.wait(timeout=10) == 1, (root / "console.txt").read_text()
    capture = json.loads((root / "controller.json").read_text())
    outcome = json.loads((root / ".state" / capture["session"] / "outcome.json").read_text())
    assert "Traceback" in outcome["failure"]["details"] and "owned early entry failure" in outcome["failure"]["details"]
    assert not outcome["log_written"] and not (root / "editor-path").exists()
    assert "No completed log for this launch" in (root / "console.txt").read_text()


def test_supervisor_loss_reaches_running_controller_through_spawning_proton_shim(supervised_notice):
    from launcher.process_exit import ProcessExit
    import threading

    process, root = supervised_notice("ongoing")
    wait_for(lambda: (root / "ongoing").exists())
    capture = json.loads((root / "controller.json").read_text())
    ended = threading.Event()
    waiter = ProcessExit(capture["pid"], sys._base_executable, lambda error: ended.set())
    waiter.start()
    try:
        bash = shutil.which("bash") or "C:/Program Files/Git/bin/bash.exe"
        subprocess.run([bash, "-c", 'kill -TERM -- "$1"', "owned", (root / "wrapper-pid").read_text()], check=True, timeout=3)
        assert process.wait(timeout=8) == 143
        assert ended.wait(1), "The controller outlived its native supervisor"
    finally:
        waiter.close()


def test_controller_exit_cancels_a_terminal_stuck_before_connecting(supervised_notice):
    # The terminal fixture never starts the inner Bash or opens the socket.
    process, root = supervised_notice("hung-terminal")
    wait_for(lambda: (root / "terminal-pid").exists())
    capture = json.loads((root / "controller.json").read_text())
    os.kill(capture["pid"], signal.SIGTERM)
    assert process.wait(timeout=8) != 0


@pytest.mark.parametrize("mode", ["failure", "missing"])
def test_failed_linux_ui_retains_notice_until_controller_owner_is_lost(supervised_notice, mode):
    process, root = supervised_notice(mode)
    expected = "fixture display failure" if mode == "failure" else "Native Linux error dialogs require zenity"
    wait_for(lambda: expected in (root / "console.txt").read_text())
    assert process.poll() is None
    capture = json.loads((root / "controller.json").read_text())
    outcome = json.loads((root / ".state" / capture["session"] / "outcome.json").read_text())
    assert outcome["failure"]["activity"]["description"] == "while saving"
    os.kill(capture["pid"], signal.SIGTERM)
    assert process.wait(timeout=8) != 0
    log = (root / "logs/launcher.log").read_text()
    assert "x" * 7000 + " END" in log and "PRESENTATION ERROR" in log
