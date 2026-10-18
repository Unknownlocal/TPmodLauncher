# SPDX-License-Identifier: MPL-2.0
"""Exercise release selection, ZIP validation and the player copy helper."""

import hashlib
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from launcher import package_release as packaging

pytestmark = pytest.mark.release


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
    (root / "launcher/owned.sh").write_bytes(b"#!/bin/bash\ntrue\n")
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
        assert "modLauncher/SteamLaunchOptions.sh" in names
        assert (package.getinfo("modLauncher/SteamLaunchOptions.sh").external_attr >> 16) & 0o111
        assert package.read("modLauncher/launcher/owned.sh") == b"#!/bin/bash\ntrue\n"
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
