# SPDX-License-Identifier: MPL-2.0
"""Test archive extraction, checksum checks and package installation using temporary files."""

import hashlib
import subprocess
import zipfile

import pytest

from launcher import prepare_release as release

pytestmark = pytest.mark.release


def test_bad_download_hash_never_replaces_existing_file(tmp_path):
    target = tmp_path / "artifact.whl"
    target.write_bytes(b"existing")
    source = tmp_path / "download-source"
    source.write_bytes(b"incorrect")
    with pytest.raises(release.dependencies.DependencyError, match="SHA-256 mismatch"):
        release.download(
            source.as_uri(),
            target,
            hashlib.sha256(b"correct").hexdigest(),
        )
    assert target.read_bytes() == b"existing"
    assert not list(tmp_path.glob("download-*.tmp"))


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


def test_pip_failure_cannot_publish_partial_packages(project_factory):
    root, wheel = project_factory()
    directory = release.prepare_packages(root, refresh_manifest=True)
    before = release.dependencies.file_inventory(directory)
    manifest = (root / "bundled_packages.toml").read_bytes()
    obsolete = directory.parent / ("0" * 64)
    obsolete.mkdir()
    (obsolete / "marker").write_bytes(b"old bundle")
    # A hash-approved but invalid archive gets as far as real pip, which must fail.
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
    # Pruning happened before real pip failed; the selected working copy is intact.
    assert not obsolete.exists()
    assert list((root / "libs/vendor").iterdir()) == [directory]
    assert release.dependencies.file_inventory(directory) == before
    assert (root / "bundled_packages.toml").read_bytes() == manifest
