# SPDX-License-Identifier: MPL-2.0
"""Test bundled Python startup, Frida imports, package damage and import isolation."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.mark.embedded
def test_relocated_release_imports_frida_without_installation(
    embedded_project, tmp_path
):
    root = embedded_project
    runtime = root / "libs/python-3.13.16-embed-amd64"
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in runtime.iterdir()
        if p.is_file()
    }
    command = [
        str(runtime / "python.exe"),
        "-B",
        str(root / "launcher/entry.py"),
        "--check-only",
    ]
    # An unrelated working directory is deliberate; paths must follow entry.py.
    first = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    second = subprocess.run(
        command, cwd=tmp_path, capture_output=True, text=True, timeout=60
    )
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first.stdout == second.stdout and "Frida 17.23.3" in first.stdout
    assert before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in runtime.iterdir()
        if p.is_file()
    }
    assert not list((root / "libs/vendor").rglob("__pycache__"))
    assert not (root / ".state").exists() and not (root / ".cache").exists()
    assert not (root / "libs/wheels").exists()


@pytest.mark.embedded
def test_entry_checks_full_distribution_version_even_when_core_version_matches(
    embedded_project,
):
    manifest = embedded_project / "bundled_packages.toml"
    original = manifest.read_bytes()
    import tomllib

    expected = tomllib.loads(original.decode())["packages"]["frida"]
    try:
        manifest.write_text(
            original.decode().replace(
                '"frida" = "' + expected + '"', '"frida" = "17.23.3+different-build"'
            )
        )
        result = subprocess.run(
            [
                str(embedded_project / "libs/python-3.13.16-embed-amd64/python.exe"),
                "-B",
                str(embedded_project / "launcher/entry.py"),
                "--check-only",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1 and "Frida did not load" in result.stderr
    finally:
        manifest.write_bytes(original)


@pytest.mark.embedded
def test_pythonpath_and_user_packages_cannot_supply_frida(embedded_project, tmp_path):
    root = embedded_project
    hostile = tmp_path / "hostile"
    (hostile / "frida").mkdir(parents=True)
    (hostile / "frida/__init__.py").write_text(
        "raise RuntimeError('foreign Frida imported')\n"
    )
    (hostile / "sitecustomize.py").write_text(
        "raise RuntimeError('foreign site imported')\n"
    )
    environment = os.environ.copy()
    environment.update(
        PYTHONPATH=str(hostile), PYTHONUSERBASE=str(hostile), PYTHONHOME=str(hostile)
    )
    command = [
        str(root / "libs/python-3.13.16-embed-amd64/python.exe"),
        "-B",
        str(root / "launcher/entry.py"),
        "--check-only",
    ]
    result = subprocess.run(
        command,
        cwd=hostile,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        result.returncode == 0
        and "Bundled dependencies ready: Frida 17.23.3" in result.stdout
    ), result.stderr


@pytest.mark.embedded
def test_entry_rejects_developer_python_instead_of_using_its_packages(embedded_project):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(embedded_project / "launcher/entry.py"),
            "--check-only",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1 and "Use this project's" in result.stderr


@pytest.mark.embedded
def test_embedded_startup_cannot_use_pip_network_or_subprocesses(
    embedded_project, tmp_path
):
    root = embedded_project
    probe = root / "launcher/readonly_probe.py"
    shutil.copyfile(FIXTURES / "readonly_startup_probe.py", probe)
    try:
        command = [
            str(root / "libs/python-3.13.16-embed-amd64/python.exe"),
            "-B",
            str(probe),
        ]
        result = subprocess.run(
            command, cwd=tmp_path, capture_output=True, text=True, timeout=60
        )
        assert (
            result.returncode == 0 and "Frida 17.23.3" in result.stdout
        ), result.stderr
    finally:
        probe.unlink()


@pytest.mark.embedded
def test_entry_reports_package_damage_without_repair(embedded_project):
    from launcher import bundled_dependencies as dependencies

    root = embedded_project
    directory = dependencies.verify_bundle(root)
    payload = directory / "frida/__init__.py"
    original = payload.read_bytes()
    try:
        payload.write_bytes(b"damaged package")
        command = [
            str(root / "libs/python-3.13.16-embed-amd64/python.exe"),
            "-B",
            str(root / "launcher/entry.py"),
            "--check-only",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert (
            result.returncode == 1
            and "Restore the complete launcher release" in result.stderr
        )
        assert "Traceback" not in result.stderr
        assert payload.read_bytes() == b"damaged package"
        assert not (root / ".state").exists() and not (root / ".cache").exists()
    finally:
        payload.write_bytes(original)


@pytest.mark.embedded
def test_entry_rejects_invalid_launch_requests_before_starting_processes(
    embedded_project,
):
    root = embedded_project
    executable = root / "libs/python-3.13.16-embed-amd64/python.exe"
    entry = root / "launcher/entry.py"
    for arguments, code, message in (
        ([], 2, "supply Steam's original"),
        (["--check-only", "--", str(executable)], 2, "cannot be combined"),
        (["--restart-grace", "nan", "--", str(executable)], 2, "finite number"),
        # Report the sibling game path, even when launched from another working directory.
        (
            ["--", str(executable)],
            1,
            f"installed game: {root.parent / 'TeamPrincipal.exe'}",
        ),
    ):
        result = subprocess.run(
            [str(executable), "-B", str(entry), *arguments],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == code and message in result.stderr, result.stderr
    assert not (root / ".state").exists() and not (root / "logs").exists()
