# SPDX-License-Identifier: MPL-2.0
"""Test package-file verification, error reporting and rejection of unsafe paths."""

from pathlib import Path
import os
import subprocess

import pytest

from launcher import bundled_dependencies as dependencies


def snapshot(root):
    """Return file bytes and modification times indexed by paths relative to root."""
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_bundle_verification_needs_only_the_release(bundled_project):
    root, directory = bundled_project
    before = snapshot(root)
    assert not (root / "pylock.toml").exists()
    assert not (root / "pyproject.toml").exists()
    assert not (root / ".cache").exists()
    assert dependencies.verify_bundle(root) == directory
    assert snapshot(root) == before


@pytest.mark.parametrize("damage", ["changed", "missing", "extra", "directory"])
def test_damaged_bundle_fails_without_repair_or_fallback(bundled_project, damage):
    root, directory = bundled_project
    payload = directory / "demo/__init__.py"
    if damage == "changed":
        payload.write_bytes(b"VALUE = 0\n")
    elif damage == "missing":
        payload.unlink()
    elif damage == "extra":
        (directory / "unexpected.py").write_bytes(b"raise RuntimeError()\n")
    else:
        # Both resolved paths remain in this pytest-owned fixture directory.
        other = directory.with_name("unselected-bundle")
        assert directory.resolve().is_relative_to(root.resolve())
        assert other.resolve().is_relative_to(root.resolve())
        directory.rename(other)
    before = snapshot(root)
    with pytest.raises(
        dependencies.DependencyError, match="Restore the complete launcher release"
    ):
        dependencies.verify_bundle(root)
    assert snapshot(root) == before
    assert not (root / ".state").exists()


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "syntax",
        "directory",
        "format",
        "digest",
        "size",
        "duplicate",
        "empty",
        "escape",
        "runtime",
        "packages",
    ],
)
def test_invalid_manifest_reports_actionable_failure(bundled_project, damage):
    root, _ = bundled_project
    path = root / "bundled_packages.toml"
    text = path.read_text()
    if damage == "missing":
        path.unlink()
    else:
        if damage == "syntax":
            text = "[broken"
        elif damage == "directory":
            text = text.replace('directory = "libs/vendor/', 'directory = "libs/other/')
        elif damage == "format":
            text = text.replace("format = 2", "format = true")
        elif damage == "digest":
            text = text.replace('sha256 = "', 'sha256 = "invalid-')
        elif damage == "size":
            import re

            text = re.sub(r"size = \d+", "size = true", text)
        elif damage == "duplicate":
            text += (
                "\n[[files]]" + text.split("[[files]]", 1)[1].split("[[files]]", 1)[0]
            )
        elif damage == "empty":
            text = text.split("[[files]]", 1)[0]
        elif damage == "runtime":
            text = text.replace('version = "3.13.16"', 'version = "garbage"')
        elif damage == "packages":
            text = text.replace('"demo" = "1.0"', '"demo" = true')
        else:
            text = text.replace('path = "COPYING"', 'path = "../outside.py"')
        path.write_text(text)
    with pytest.raises(
        dependencies.DependencyError, match="Cannot (read|verify) bundled"
    ):
        dependencies.verify_bundle(root)
    assert not (root.parent / "outside.py").exists()


def test_git_line_endings_and_comments_do_not_change_bundle_selection(bundled_project):
    root, directory = bundled_project
    path = root / "bundled_packages.toml"
    text = path.read_text()
    path.write_bytes(
        ("# Local formatting comment\n" + text).replace("\n", "\r\n").encode()
    )
    assert dependencies.verify_bundle(root) == directory


@pytest.mark.parametrize(
    "relative",
    [
        "../outside",
        "/absolute",
        "C:/absolute",
        "libs/../outside",
        "libs/NUL.py",
        "libs/file.py.",
        "libs//file",
        "libs\\file",
        "libs/a:stream",
    ],
)
def test_local_paths_cannot_escape_or_alias_project(tmp_path, relative):
    with pytest.raises(dependencies.DependencyError):
        dependencies.local_path(tmp_path, relative)


@pytest.mark.parametrize("location", ["ancestor", "inside"])
def test_real_directory_links_cannot_supply_packages(
    bundled_project, tmp_path, location
):
    root, directory = bundled_project
    outside = tmp_path / "outside"
    outside.mkdir()
    if location == "ancestor":
        link = root / "libs"
        # Move only this fixture's tree, so the link has real readable contents.
        moved = outside / "libs"
        link.rename(moved)
        destination = moved
    else:
        link = directory / "redirect"
        destination = outside
    if os.name == "nt":
        # Junctions need no symlink privilege on Windows; both paths are pytest-owned.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(destination)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(destination, target_is_directory=True)
    try:
        with pytest.raises(dependencies.DependencyError, match="Linked"):
            dependencies.verify_bundle(root)
    finally:
        # Remove the link itself, never recursively remove the redirected tree.
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
