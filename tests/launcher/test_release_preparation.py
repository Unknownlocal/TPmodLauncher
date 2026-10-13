# SPDX-License-Identifier: MPL-2.0
"""Test archive extraction, checksum checks and package installation using temporary files."""

from textwrap import dedent
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from launcher import prepare_release as release


def test_bad_download_hash_never_replaces_existing_file(tmp_path):
    target = tmp_path / "artifact.whl"
    target.write_bytes(b"existing")
    source = tmp_path / "download-source"
    source.write_bytes(b"incorrect")
    with pytest.raises(release.dependencies.DependencyError, match="SHA-256 mismatch"):
        release.download(
            source.as_uri(), target, hashlib.sha256(b"correct").hexdigest()
        )
    assert target.read_bytes() == b"existing"
    assert not list(tmp_path.glob("download-*.tmp"))


def test_verified_download_is_reused_without_network(tmp_path):
    target = tmp_path / "artifact.whl"
    target.write_bytes(b"verified")
    # This source does not exist: successful reuse cannot involve a download.
    release.download(
        (tmp_path / "absent-source").as_uri(),
        target,
        hashlib.sha256(b"verified").hexdigest(),
    )


def test_runtime_is_extracted_and_existing_copy_checked_without_overwrite(tmp_path):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("python.exe", b"synthetic fixture")
        output.writestr("python313._pth", b"python313.zip\n.\n")
    runtime = {"directory": "libs/python-3.13.16-embed-amd64"}
    release.prepare_runtime(tmp_path, runtime, archive)
    target = tmp_path / runtime["directory"]
    release.prepare_runtime(tmp_path, runtime, archive)
    (target / "extra.py").write_text("unexpected")
    with pytest.raises(release.dependencies.DependencyError, match="not overwritten"):
        release.prepare_runtime(tmp_path, runtime, archive)
    assert (target / "extra.py").read_text() == "unexpected"


def test_unsafe_runtime_archive_does_not_escape_libs(tmp_path):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../escape.py", b"escape")
    with pytest.raises(release.dependencies.DependencyError, match="Unsafe path"):
        release.prepare_runtime(tmp_path, {"directory": "libs/python"}, archive)
    assert not (tmp_path / "libs/escape.py").exists()


def test_pip_prepares_reproducible_bundle_with_licence_and_no_local_paths(
    project_factory,
):
    root, _ = project_factory()
    directory = release.prepare_packages(root, refresh_manifest=True)
    assert (directory / "demo/__init__.py").read_text() == "VALUE = 42\n"
    assert (directory / "demo-1.0.dist-info/licenses/LICENSE").exists()
    assert not list(directory.rglob("*.pyc"))
    assert not list(directory.rglob("direct_url.json"))
    manifest = (root / "bundled_packages.toml").read_bytes()
    assert str(root).encode() not in manifest
    before = release.dependencies.file_inventory(directory)
    vendor = directory.parent
    # Setup should remove both earlier folder layouts, but keep unrelated and live build folders.
    obsolete = [vendor / ("0" * 64), vendor / ("1" * 64 + "-" + "2" * 32)]
    retained = [vendor / "notes", vendor / ".package-build-other"]
    for folder in obsolete + retained:
        (folder / "nested").mkdir(parents=True)
        (folder / "nested/marker").write_bytes(b"fixture contents")
    # Provenance-only changes do not select a new runtime directory or manifest.
    lock_path = root / "pylock.toml"
    lock_path.write_text(
        lock_path.read_text().replace(
            "hashes =",
            'url = "https://example.invalid/demo-1.0-py3-none-any.whl"\nhashes =',
        )
    )
    assert release.prepare_packages(root) == directory
    assert (root / "bundled_packages.toml").read_bytes() == manifest
    assert release.dependencies.file_inventory(directory) == before
    assert all(not folder.exists() for folder in obsolete)
    assert all(
        (folder / "nested/marker").read_bytes() == b"fixture contents"
        for folder in retained
    )
    assert set(vendor.iterdir()) == {directory, *retained}
    assert not (root / ".state").exists()
    # Explicit refresh still runs real pip and reproduces the reviewed files.
    assert release.prepare_packages(root, refresh_manifest=True) == directory
    assert (root / "bundled_packages.toml").read_bytes() == manifest
    assert release.dependencies.file_inventory(directory) == before


def test_verified_matching_bundle_is_reused_without_cached_wheels(project_factory):
    root, wheel = project_factory()
    directory = release.prepare_packages(root, refresh_manifest=True)
    before = release.dependencies.file_inventory(directory)
    wheel.unlink()
    # Installing would fail without the wheel; a verified existing bundle needs no pip run.
    assert release.prepare_packages(root) == directory
    assert release.dependencies.file_inventory(directory) == before


def test_missing_selected_bundle_is_installed_from_cached_wheels(project_factory):
    root, _ = project_factory()
    directory = release.prepare_packages(root, refresh_manifest=True)
    before = release.dependencies.file_inventory(directory)
    manifest = (root / "bundled_packages.toml").read_bytes()
    # Delete only this fixture's published folder to simulate a missing installation.
    assert directory.resolve().parent == (root / "libs/vendor").resolve()
    shutil.rmtree(directory)
    assert release.prepare_packages(root) == directory
    assert release.dependencies.file_inventory(directory) == before
    assert (root / "bundled_packages.toml").read_bytes() == manifest


@pytest.mark.parametrize("change", ["package", "runtime"])
def test_existing_bundle_is_not_reused_for_different_requirements(
    project_factory, change
):
    root, wheel = project_factory()
    directory = release.prepare_packages(root, refresh_manifest=True)
    before = release.dependencies.file_inventory(directory)
    if change == "package":
        path = root / "pylock.toml"
        path.write_text(
            path.read_text()
            .replace('version = "1.0"', 'version = "1.1"')
            .replace("demo-1.0-py3-none-any.whl", "demo-1.1-py3-none-any.whl")
        )
        wheel = wheel.with_name("demo-1.1-py3-none-any.whl")
    else:
        path = root / "pyproject.toml"
        path.write_text(
            path.read_text().replace('version = "3.13.16"', 'version = "3.13.17"')
        )
    # A needed installation must check its inputs instead of returning the old bundle.
    wheel.write_bytes(b"corrupt wheel")
    with pytest.raises(
        release.dependencies.DependencyError, match="Build wheel SHA-256 mismatch"
    ):
        release.prepare_packages(root)
    assert release.dependencies.file_inventory(directory) == before


def test_vendor_cleanup_requires_a_valid_selected_bundle(bundled_project):
    root, directory = bundled_project
    obsolete = directory.parent / ("0" * 64)
    obsolete.mkdir()
    (obsolete / "marker").write_bytes(b"old bundle")
    (directory / "demo/__init__.py").write_bytes(b"changed")
    with pytest.raises(release.dependencies.DependencyError, match="Cannot verify"):
        release.prune_vendor(root)
    assert (obsolete / "marker").read_bytes() == b"old bundle"
    assert (directory / "demo/__init__.py").read_bytes() == b"changed"


@pytest.mark.parametrize("location", ["folder", "inside"])
def test_vendor_cleanup_checks_all_links_before_deleting_any_folder(
    bundled_project, tmp_path, location
):
    root, directory = bundled_project
    obsolete = directory.parent / ("0" * 64)
    obsolete.mkdir()
    (obsolete / "marker").write_bytes(b"old bundle")
    candidate = directory.parent / ("1" * 64)
    outside = tmp_path / "outside-vendor"
    outside.mkdir()
    (outside / "marker").write_bytes(b"outside vendor")
    if location == "folder":
        link = candidate
    else:
        candidate.mkdir()
        link = candidate / "redirect"
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(release.dependencies.DependencyError, match="Linked"):
            release.prune_vendor(root)
        assert (obsolete / "marker").read_bytes() == b"old bundle"
        assert (outside / "marker").read_bytes() == b"outside vendor"
        assert release.dependencies.verify_bundle(root) == directory
    finally:
        # Remove this fixture's link itself; never delete the redirected directory.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_default_build_requires_reviewed_manifest(project_factory):
    root, _ = project_factory()
    with pytest.raises(
        release.dependencies.DependencyError, match="--refresh-manifest"
    ):
        release.prepare_packages(root)
    assert not list((root / "libs/vendor").iterdir())
    assert not (root / "bundled_packages.toml").exists()


def test_existing_altered_package_bundle_is_not_overwritten(project_factory):
    root, _ = project_factory()
    directory = release.prepare_packages(root, refresh_manifest=True)
    payload = directory / "demo/__init__.py"
    payload.write_bytes(b"changed")
    with pytest.raises(release.dependencies.DependencyError, match="Cannot verify"):
        release.prepare_packages(root)
    assert payload.read_bytes() == b"changed"


def test_build_hash_failure_does_not_change_published_bundle(project_factory):
    root, wheel = project_factory()
    directory = release.prepare_packages(root, refresh_manifest=True)
    obsolete = directory.parent / ("0" * 64)
    obsolete.mkdir()
    (obsolete / "marker").write_bytes(b"old bundle")
    wheel.write_bytes(b"corrupt wheel")
    before = release.dependencies.file_inventory(directory)
    with pytest.raises(
        release.dependencies.DependencyError, match="Build wheel SHA-256 mismatch"
    ):
        release.prepare_packages(root, refresh_manifest=True)
    assert release.dependencies.file_inventory(directory) == before
    assert not obsolete.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_pip_failure_cannot_publish_partial_packages(project_factory, existing):
    root, wheel = project_factory()
    directory = None
    if existing:
        directory = release.prepare_packages(root, refresh_manifest=True)
        before = release.dependencies.file_inventory(directory)
        manifest = (root / "bundled_packages.toml").read_bytes()
        obsolete = directory.parent / ("0" * 64)
        obsolete.mkdir()
        (obsolete / "marker").write_bytes(b"old bundle")
    # A hash-approved but invalid archive gets as far as real pip, which must fail.
    import subprocess

    bad = b"not a wheel archive"
    wheel.write_bytes(bad)
    lock = root / "pylock.toml"
    import re

    lock.write_text(
        re.sub(
            r'sha256 = "[a-f0-9]+"',
            f'sha256 = "{hashlib.sha256(bad).hexdigest()}"',
            lock.read_text(),
        )
    )
    with pytest.raises(subprocess.CalledProcessError):
        release.prepare_packages(root, refresh_manifest=True)
    if existing:
        # Pruning happened before real pip failed; the selected working copy is intact.
        assert not obsolete.exists()
        assert list((root / "libs/vendor").iterdir()) == [directory]
        assert release.dependencies.file_inventory(directory) == before
        assert (root / "bundled_packages.toml").read_bytes() == manifest
    else:
        assert not list((root / "libs/vendor").iterdir())
        assert not (root / "bundled_packages.toml").exists()


def test_target_python_markers_are_checked_independently_of_developer_python(
    project_factory,
):
    # This dependency applies to embedded Python 3.13 even when the build Python is newer.
    root, _ = project_factory(
        metadata="Name: demo\nVersion: 1.0\nRequires-Python: >=3.13\n"
        'Requires-Dist: missing-package; python_version < "3.14"\n'
    )
    with pytest.raises(
        release.dependencies.DependencyError,
        match="Unfulfilled embedded-runtime requirement",
    ):
        release.prepare_packages(root, refresh_manifest=True)
    assert not list((root / "libs/vendor").iterdir())
    assert not (root / "bundled_packages.toml").exists()


def test_normalized_package_metadata_matches_standard_lock(project_factory, tmp_path):
    # Names and versions can use equivalent spellings in the lock and installed metadata.
    root, _ = project_factory()
    lock = root / "pylock.toml"
    lock.write_text(
        lock.read_text()
        .replace('name = "demo"', 'name = "example-package"')
        .replace("demo-1.0-py3-none-any.whl", "example_package-1.0-py3-none-any.whl")
        .replace('version = "1.0"', 'version = "1.0.0"')
    )
    info = tmp_path / "example_package-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Name: example_package\nVersion: 1.0\nRequires-Python: >=3.13\n"
    )
    runtime = release.read_settings(root)["runtime"]
    release.validate_packages(tmp_path, release.read_lock(root, runtime), runtime)


@pytest.mark.parametrize(
    "damage", ["syntax", "version", "hash", "format", "wheel-path", "platform"]
)
def test_invalid_build_lock_stops_preparation(project_factory, damage):
    root, _ = project_factory()
    path = root / "pylock.toml"
    text = path.read_text()
    if damage == "syntax":
        text = "[broken"
    elif damage == "version":
        text = text.replace('version = "1.0"', 'version = "1.0 --extra-option"')
    elif damage == "hash":
        text = text.replace('sha256 = "', 'sha256 = "bad-')
    elif damage == "format":
        text = text.replace('lock-version = "1.0"', 'lock-version = "2.0"')
    elif damage == "wheel-path":
        text = text.replace('path = ".cache/wheels/', 'path = "../')
    else:
        text = text.replace("sys_platform == 'win32'", "sys_platform == 'linux'")
    path.write_text(text)
    with pytest.raises(release.dependencies.DependencyError, match="dependency lock"):
        release.prepare_packages(root)
    assert not (root / "libs").exists()


def test_lock_markers_select_only_embedded_runtime_dependencies(project_factory):
    root, _ = project_factory()
    lock = root / "pylock.toml"
    # A package conditional on older Python need not have a Windows wheel for this release.
    lock.write_text(
        lock.read_text() + dedent("""\

            [[packages]]
            name = "old-python-helper"
            version = "1.0"
            marker = "python_version < '3.11'"
            [[packages.wheels]]
            name = "old_python_helper-1.0-py3-none-any.whl"
            path = ".cache/wheels/old_python_helper-1.0-py3-none-any.whl"
            hashes = {sha256 = "0000000000000000000000000000000000000000000000000000000000000000"}
            """),
    )
    runtime = release.read_settings(root)["runtime"]
    assert [package.name for package, _ in release.read_lock(root, runtime)] == ["demo"]
    assert (
        release.prepare_packages(root, refresh_manifest=True) / "demo/__init__.py"
    ).exists()
