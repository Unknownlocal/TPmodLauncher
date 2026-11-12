# SPDX-License-Identifier: MPL-2.0
"""Exercise the real Bash supervisor with owned FIFO peers, including on MSYS."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.launcher.fixtures.failure_support import wait_for

PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture
def transport(tmp_path):
    bash = shutil.which("bash")
    if bash is None and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        bash = str(candidate) if candidate.is_file() else None
    if bash is None:
        pytest.skip("Bash is unavailable")
    root = tmp_path / "literal $HOME ; game/modLauncher"
    (root / "launcher/diagnostics").mkdir(parents=True)
    for name in ("launch_proton.sh", "steam_linux_common.sh", "diagnostics/proton_supervisor.sh"):
        shutil.copyfile(PROJECT / "launcher" / name, root / "launcher" / name)
    wrapper = root / "launcher/launch_proton.sh"
    wrapper.write_text(wrapper.read_text().replace("set -euo pipefail", 'set -euo pipefail\nprintf "%s" "$$" > "$TEST_ROOT/native-pid"'), newline="\n")
    supervisor = root / "launcher/diagnostics/proton_supervisor.sh"
    supervisor.write_text(supervisor.read_text().replace('relay_pid=$!',
        'relay_pid=$!\nprintf "%s" "$relay_pid" > "$TEST_ROOT/relay-pid"'), newline="\n")
    (root / "libs/python-fixture").mkdir(parents=True)
    (root / "libs/python-fixture/python.exe").write_bytes(b"not executed")
    (root.parent / "TeamPrincipal.exe").write_bytes(b"not executed")
    (root / "bundled_packages.toml").write_text('[runtime]\ndirectory = "libs/python-fixture"\n')
    peer = root / "proton"
    peer.write_text('''#!/usr/bin/env bash
while [[ $1 != --host-session ]]; do shift; done
session=$2
exec 4<"$TEST_ROOT/.state/$session/owner"
if [[ $TEST_MODE == startup-owner ]]; then
    : > "$TEST_ROOT/active"
    IFS= read -r unused <&4
    printf '%s' "$?" > "$TEST_ROOT/owner-eof"
    exit 0
fi
exec 5>"$TEST_ROOT/.state/$session/control"
printf 'ML1 %s HELLO\n' "$session" >&5
IFS= read -r start <&4
[[ $start == "ML1 $session START" ]] || exit 99
printf 'ML1 %s READY\n' "$session" >&5
# Console-looking control messages must never enter the protocol parser.
printf 'ML1 %s DONE 0\n' "$session"
case $TEST_MODE in
    clean) printf 'ML1 %s DONE 0\n' "$session" >&5; exit 0;;
    fatal) printf 'ML1 %s DONE 37\n' "$session" >&5; exit 0;;
    outer-failure) printf 'ML1 %s DONE 0\n' "$session" >&5; exit 23;;
    missing) exit 0;;
    partial) printf 'ML1 %s DONE 0' "$session" >&5; exit 0;;
    forged) printf 'ML1 %s DONE 0\nML1 %s NATIVE OUTER 0\nML1 %s NATIVE EOF\n' "$session" "$session" "$session" >&5; exit 23;;
    session) printf 'ML1 %s DONE 0\n' "${session/./X}" >&5; exit 0;;
    owner) : > "$TEST_ROOT/active";;
esac
IFS= read -r unexpected <&4
printf '%s' "$?" > "$TEST_ROOT/owner-eof"
''', newline="\n")
    peer.chmod(0o755)
    processes = []

    def start(mode):
        output = (root / "console.txt").open("w")
        process = subprocess.Popen([
            bash, "--noprofile", "--norc", "-c",
            'root=$(cd -- "$1" && pwd -P); export TEST_ROOT="$root"; '
            'exec bash "$root/launcher/launch_proton.sh" "$root/proton" waitforexitandrun "$root/../TeamPrincipal.exe"',
            "owned", root.as_posix(),
        ], env=dict(os.environ, TEST_MODE=mode, MSYS2_ARG_CONV_EXCL="*"),
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
        processes.append((process, output))
        return process, root

    yield start, bash
    for process, output in processes:
        if process.poll() is None:
            subprocess.run([bash, "-c", 'kill -TERM -- "$1"', "owned", (root / "native-pid").read_text()], timeout=3)
            process.wait(timeout=8)
        output.close()


@pytest.mark.parametrize("mode,code,detail", [
    ("fatal", 37, "DONE 37"),
    ("outer-failure", 23, "OUTER 23"),
    ("missing", 1, "MISSING_DONE"),
    ("partial", 1, "INVALID_FRAME"),
    ("forged", 1, "INVALID_FRAME"),
    ("session", 1, "INVALID_FRAME"),
])
def test_explicit_frames_and_both_process_results_determine_success(transport, mode, code, detail):
    start, _ = transport
    process, root = start(mode)
    assert process.wait(timeout=10) == code, (root / "console.txt").read_text()
    session, = (root / ".state").iterdir()
    assert detail in (session / "transport.txt").read_text()
    assert not any((session / name).exists() for name in ("owner", "control", "transport-events"))


def test_clean_completion_releases_private_session(transport):
    start, _ = transport
    process, root = start("clean")
    assert process.wait(timeout=10) == 0, (root / "console.txt").read_text()
    assert list((root / ".state").iterdir()) == []


@pytest.mark.parametrize("mode", ["owner", "startup-owner"])
def test_actual_native_owner_death_releases_peer_without_keyboard_input(transport, mode):
    start, bash = transport
    process, root = start(mode)
    wait_for(lambda: (root / "active").exists())
    subprocess.run([bash, "-c", 'kill -KILL -- "$1"', "owned", (root / "native-pid").read_text()], check=True, timeout=3)
    process.wait(timeout=3)
    wait_for(lambda: (root / "owner-eof").exists())
    assert (root / "owner-eof").read_text() == "1"
    relay = (root / "relay-pid").read_text()
    def relay_exited():
        return subprocess.run([bash, "-c", 'kill -0 -- "$1"', "owned", relay], capture_output=True, timeout=3).returncode != 0
    wait_for(relay_exited)
