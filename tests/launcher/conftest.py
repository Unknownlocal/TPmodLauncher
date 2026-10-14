# SPDX-License-Identifier: MPL-2.0
"""Real Git source fixtures shared by launcher build and source-package tests."""

import pytest

from launcher import build_frida as build


@pytest.fixture
def source_repository():
    """Return a factory; children are (path, commit) submodule pairs."""

    def create(directory, children=()):
        directory.mkdir(parents=True, exist_ok=True)
        build.git(directory, "init")
        build.git(directory, "config", "user.name", "Build fixture")
        build.git(directory, "config", "user.email", "fixture@example.invalid")
        (directory / "code.py").write_text("VALUE = 1\n")
        build.git(directory, "add", "code.py")
        for name, revision in children:
            build.git(
                directory,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{revision},{name}",
            )
        build.git(directory, "commit", "-m", "Synthetic source fixture")
        return build.git(directory, "rev-parse", "HEAD")

    return create
