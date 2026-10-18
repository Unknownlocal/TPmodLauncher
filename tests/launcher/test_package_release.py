# SPDX-License-Identifier: MPL-2.0
"""Exercise release selection, ZIP validation and the player copy helper."""

import hashlib
import json
from pathlib import PureWindowsPath
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from launcher import package_release as packaging
from launcher import steam_launch_options as helper


@pytest.fixture
def retained_artifacts(tmp_path, monkeypatch):
    cache = tmp_path / ".cache/reduced-frida"
    correct = cache / "selected-build"
    wrong = cache / "other-build"
    for directory, data in ((correct, b"selected wheel"), (wrong, b"different wheel")):
        directory.mkdir(parents=True)
        (directory / "frida.whl").write_bytes(data)
        (directory / "build-details.json").write_text(directory.name)
    pin = SimpleNamespace(hashes={"sha256": hashlib.sha256(b"selected wheel").hexdigest()},
                          path=".cache/wheels/frida.whl")
    monkeypatch.setattr(packaging.release, "read_settings", lambda root: {"runtime": {}})
    monkeypatch.setattr(packaging.release, "read_lock", lambda root, runtime: [(SimpleNamespace(name="frida"), pin)])
    monkeypatch.setattr(packaging.build, "read_recipe", lambda path: {})
    monkeypatch.setattr(packaging.build, "verify_output", lambda directory, recipe, target: directory / "frida.whl")
    for name, directory in (("matching-source", correct), ("other-source", wrong)):
        output = cache / name
        output.mkdir()
        archive = output / "frida-sources.tar.xz"
        archive.write_bytes(b"owned source fixture")
        (output / "source-details.json").write_text(json.dumps({
            "build_details_sha256": packaging.build.file_record(directory / "build-details.json")["sha256"],
            "archive": packaging.build.file_record(archive),
        }))
    return tmp_path, correct, cache / "matching-source", wrong


def test_automatic_discovery_matches_the_lock_and_exact_source_record(retained_artifacts):
    root, build, source, _ = retained_artifacts
    result = packaging.resolve_inputs(root)
    assert result["build"] == str(build.resolve())
    assert result["sources"] == str(source.resolve())
    assert result["wheel_cache"] == str(root / ".cache/wheels/frida.whl")


def test_explicit_build_cannot_override_the_pinned_hash(retained_artifacts):
    root, _, _, wrong = retained_artifacts
    with pytest.raises(RuntimeError, match="No retained Windows Frida build matches"):
        packaging.resolve_inputs(root, build_output=wrong)


def test_explicit_source_cannot_override_build_matching(retained_artifacts):
    root, _, source, _ = retained_artifacts
    with pytest.raises(RuntimeError, match="differs from its receipt or binary build"):
        packaging.resolve_inputs(root, source_output=source.with_name("other-source"))


def test_missing_source_export_fails_before_preparation(retained_artifacts):
    root, _, source, _ = retained_artifacts
    (source / "source-details.json").unlink()
    with pytest.raises(RuntimeError, match="No matching Frida source export"):
        packaging.resolve_inputs(root)


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "game with spaces/modLauncher"
    vendor = root / "libs/vendor/stage"
    vendor.mkdir(parents=True)
    (vendor / "demo.py").write_text("# owned dependency fixture\n")
    runtime_settings = {"version": "3.13.16", "directory": "libs/python-3.13.16-embed-amd64"}
    inventory = packaging.paths.file_inventory(vendor)
    (root / "bundled_packages.toml").write_bytes(packaging.release.manifest_bytes(
        runtime_settings, [(SimpleNamespace(name="demo", version="1.0"), None)], inventory,
    ))
    vendor.rename(root / packaging.release.package_directory(inventory))
    for name in packaging.ROOT_FILES:
        if name != "bundled_packages.toml":
            (root / name).write_text("owned fixture\n")
    for directory in (*packaging.SOURCE_DIRECTORIES, "notices", "libs/mod-runners"):
        (root / directory).mkdir(parents=True)
        (root / directory / "owned.py").write_text("# owned fixture\n")
    manifest = packaging.paths.read_manifest(root)
    runtime = root / manifest["runtime"]["directory"]
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"inert runtime fixture")
    material = root / "frida-source-fixture.txt"
    material.write_text("owned source fixture")
    for name in (".venv/private.txt", ".cache/private.txt", "logs/launcher.log",
                 "libs/vendor/obsolete/private.txt", "examples/theme_mod/settings.json",
                 "launcher/__pycache__/owned.pyc", "docs/local/private.md"):
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"must not ship")
    return root, manifest, {"source/frida/fixture.txt": material}


def test_archive_is_relocatable_selected_and_repeatable(inputs):
    root, manifest, materials = inputs
    checks = []

    def check(extracted):
        assert extracted != root and extracted.name == "modLauncher"
        assert (extracted / "bundled_packages.toml").is_file()
        assert (extracted / manifest["directory"]).is_dir()
        assert (extracted / "source/frida/fixture.txt").read_text() == "owned source fixture"
        assert b"must not ship" not in [file.read_bytes() for file in extracted.rglob("*") if file.is_file()]
        checks.append(extracted)

    archive = packaging.package_release(root, "test.1", manifest, materials, checker=check)
    original = archive.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    assert checksum.read_text() == f"{digest}  {archive.name}\n"
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert names == sorted(names)
        assert all(name.startswith("modLauncher/") for name in names)
        assert "modLauncher/SteamLaunchOptions.cmd" in names
        assert "modLauncher/VERSION.txt" in names
        assert b"%~dp0libs\\python-3.13.16-embed-amd64\\python.exe" in package.read("modLauncher/SteamLaunchOptions.cmd")
    with pytest.raises(RuntimeError, match="already exists"):
        packaging.package_release(root, "test.1", manifest, materials, checker=check)
    assert archive.read_bytes() == original
    # Delete only these two known artifacts inside the pytest-owned fixture directory.
    archive.unlink()
    checksum.unlink()
    packaging.package_release(root, "test.1", manifest, materials, checker=check)
    assert archive.read_bytes() == original and len(checks) == 2


def test_failed_extracted_verification_produces_no_archive(inputs):
    root, manifest, materials = inputs

    def fail(_):
        raise subprocess.CalledProcessError(1, "owned verification fixture")

    with pytest.raises(subprocess.CalledProcessError):
        packaging.package_release(root, "bad", manifest, materials, checker=fail)
    assert list((root / "dist").iterdir()) == []


@pytest.mark.parametrize("version", ["../escape", "bad/name", "bad version", "", "a" * 65])
def test_invalid_version_cannot_escape_output(inputs, version):
    root, manifest, materials = inputs
    with pytest.raises(RuntimeError, match="Version"):
        packaging.package_release(root, version, manifest, materials)
    assert not (root / "dist").exists()


def test_helper_uses_installed_paths_and_manifest_runtime():
    root = PureWindowsPath(r"D:\Steam Library\Game\modLauncher")
    line = helper.launch_line(root, {"runtime": {"directory": "libs/python-example"}})
    assert line == ('"D:\\Steam Library\\Game\\modLauncher\\libs\\python-example\\python.exe" '
                    '-B "modLauncher\\launcher\\entry.py" -- %command%')


def test_helper_reports_wrong_installation(inputs, monkeypatch, capsys):
    root, _, _ = inputs
    monkeypatch.setattr(helper, "__file__", str(root / "launcher/steam_launch_options.py"))
    monkeypatch.setattr(helper, "WINDOWS_HOST", True)
    assert helper.main() == 1
    assert "directly beside TeamPrincipal.exe" in capsys.readouterr().err


def test_wsl_verification_converts_script_path(inputs, monkeypatch):
    root, _, _ = inputs
    calls = []
    monkeypatch.setattr(packaging, "WINDOWS_HOST", False)

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "C:\\extracted\\launcher\\entry.py\n" if command[0] == "wslpath" else "ready\n", "")

    monkeypatch.setattr(packaging.subprocess, "run", run)
    packaging.check_release(root)
    assert calls[0] == ["wslpath", "-w", str(root / "launcher/entry.py")]
    assert calls[1][2:] == ["C:\\extracted\\launcher\\entry.py", "--check-only"]
