# SPDX-License-Identifier: MPL-2.0
"""Test package-file verification, error reporting and rejection of unsafe paths."""


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


@pytest.mark.parametrize("damage", ["changed", "missing"])
def test_damaged_bundle_fails_without_repair_or_fallback(bundled_project, damage):
    root, directory = bundled_project
    payload = directory / "demo/__init__.py"
    if damage == "changed":
        payload.write_bytes(b"VALUE = 0\n")
    else:
        payload.unlink()
    before = snapshot(root)
    with pytest.raises(
        dependencies.DependencyError, match="Restore the complete launcher release"
    ):
        dependencies.verify_bundle(root)
    assert snapshot(root) == before
    assert not (root / ".state").exists()
