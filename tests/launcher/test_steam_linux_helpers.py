# SPDX-License-Identifier: MPL-2.0
"""Check native Linux helper output and Proton argv rewriting with owned processes."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

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
    for name in ("bash", "dirname", "realpath", "cat"):
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


def test_proton_wrapper_keeps_prefix_environment_arguments_and_exit_status(installed):
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
    game_args = ["--save", "quotes ' $value ; `literal`", ""]
    result = subprocess.run([
        "/bin/bash", str(root / "launcher/launch_proton.sh"),
        str(outer), "--marker", "space value", str(proton), "waitforexitandrun",
        str(root.parent / "TeamPrincipal.exe"), *game_args,
    ], env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 23, result.stderr
    record = json.loads(capture.read_text())
    assert record["prefix"] == "owned prefix with spaces"
    assert record["args"][:4] == ["waitforexitandrun", str(root / "libs/python-fixture/python.exe"), "-B", "-c"]
    assert "proton_entry.py" in record["args"][4]
    assert record["args"][5:] == game_args
    # Execute the exact captured bootstrap against an owned entry stub. Stand in
    # for Wine's mapped sys.executable; no fixed drive or host path is required.
    shutil.copyfile(PROJECT / "launcher/proton_entry.py", root / "launcher/proton_entry.py")
    (root / "launcher/entry.py").write_text(
        "import json\nfrom pathlib import Path\ndef main(args):\n"
        "    Path(__file__).with_name('received.json').write_text(json.dumps(args))\n    return 31\n"
    )
    bootstrap_result = subprocess.run([
        sys.executable, "-B", "-c", "import sys;sys.executable=sys.argv.pop(1);" + record["args"][4],
        str(root / "libs/python-fixture/python.exe"), *game_args,
    ], env=environment, capture_output=True, text=True, timeout=10)
    assert bootstrap_result.returncode == 31, bootstrap_result.stderr
    assert json.loads((root / "launcher/received.json").read_text()) == [
        "--", str(root.parent / "TeamPrincipal.exe"), *game_args,
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
