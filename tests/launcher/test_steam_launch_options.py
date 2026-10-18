# SPDX-License-Identifier: MPL-2.0
"""Check clipboard transport and manual fallback without touching the real clipboard."""

import base64
import re
import subprocess

import pytest

from launcher import steam_launch_options as helper


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = tmp_path / "game folder/modLauncher"
    interpreter = root / "libs/python-fixture/python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"inert owned runtime fixture")
    (root.parent / "TeamPrincipal.exe").write_bytes(b"inert owned game fixture")
    (root / "bundled_packages.toml").write_text(
        'format = 2\ndirectory = "libs/vendor/fixture"\n'
        '[runtime]\nversion = "3.13.16"\ndirectory = "libs/python-fixture"\n'
        '[packages]\nfrida = "1.0"\n'
    )
    monkeypatch.setattr(helper, "__file__", str(root / "launcher/steam_launch_options.py"))
    monkeypatch.setattr(helper, "WINDOWS_HOST", True)
    return root


def test_copies_exact_displayed_line_with_relative_script(installed, monkeypatch, capsys):
    copied = []
    monkeypatch.setattr(helper, "copy_to_clipboard", copied.append)
    assert helper.main() == 0
    output = capsys.readouterr().out
    assert len(copied) == 1 and copied[0] in output
    assert copied[0].endswith(' -B "modLauncher\\launcher\\entry.py" -- %command%')
    assert "Copied to clipboard" in output and "Ctrl+V" in output


@pytest.mark.parametrize("error", [
    OSError("clipboard unavailable"),
    subprocess.CalledProcessError(1, "owned fixture"),
    subprocess.TimeoutExpired("owned fixture", 10),
])
def test_clipboard_failure_keeps_working_manual_instructions(installed, monkeypatch, capsys, error):
    def fail(line):
        raise error
    monkeypatch.setattr(helper, "copy_to_clipboard", fail)
    assert helper.main() == 0
    output = capsys.readouterr().out
    assert '"modLauncher\\launcher\\entry.py" -- %command%' in output
    assert "Copy the displayed line manually" in output
    assert "Copied to clipboard" not in output


def test_unicode_and_shell_characters_are_copied_as_data(tmp_path, monkeypatch):
    system = tmp_path / "Windows fixture"
    monkeypatch.setenv("SystemRoot", str(system))
    line = '"D:\\Games\\L\'été 日本語 $(&test)\\python.exe" -B "modLauncher\\launcher\\entry.py" -- %command%'
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
    monkeypatch.setattr(helper.subprocess, "run", run)
    helper.copy_to_clipboard(line)
    command, options = calls[0]
    assert command[0] == str(system / "System32/WindowsPowerShell/v1.0/powershell.exe")
    assert command[1:4] == ["-NoProfile", "-NonInteractive", "-Command"]
    assert line not in command[-1]
    encoded = re.search(r"FromBase64String\('([^']+)'\)", command[-1]).group(1)
    assert base64.b64decode(encoded).decode("utf-16le") == line
    assert options["check"] and options["timeout"] == 10
    assert not options.get("shell", False)


def test_invalid_installation_does_not_overwrite_clipboard(installed, monkeypatch, capsys):
    (installed.parent / "TeamPrincipal.exe").unlink()
    def forbidden(line):
        pytest.fail("An invalid installation must not modify the clipboard")
    monkeypatch.setattr(helper, "copy_to_clipboard", forbidden)
    assert helper.main() == 1
    assert "directly beside TeamPrincipal.exe" in capsys.readouterr().err
