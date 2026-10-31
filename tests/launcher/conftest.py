# SPDX-License-Identifier: MPL-2.0
"""Real Git source fixtures shared by launcher build and source-package tests."""

from pathlib import Path
import shutil

import pytest

from tests.regulation_support import make_mods

from launcher import build_frida as build
from launcher import build_mod


@pytest.fixture(scope="session")
def built_menu_mod(tmp_path_factory):
    """Exercise the legacy author builder with current menu example source."""
    root = Path(__file__).resolve().parents[2]
    folder = tmp_path_factory.mktemp("owned-menu-contract")
    shutil.copytree(root / "examples/menu_mod/src", folder / "src")
    (folder / "mod.toml").write_text('''schema = 1
id = "example.menu"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
component = "mod.wasm"
capabilities = ["diagnostics", "main-menu"]
''', encoding="utf-8")
    build_mod.build(folder, folder / "mod.wasm")
    return folder


@pytest.fixture(scope="session")
def built_python_mod(tmp_path_factory, legacy_diagnostic_manifest):
    """Build one owned Python component for both normal and shipped-interpreter checks."""
    folder = tmp_path_factory.mktemp("owned-python-contract")
    (folder / "mod.toml").write_text(legacy_diagnostic_manifest, encoding="utf-8")
    shutil.copytree(
        Path(__file__).with_name("fixtures") / "contract_mod", folder / "src"
    )
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("MODLAUNCHER_TEST_SECRET", "build-secret-not-for-guests")
        build_mod.build(folder, folder / "mod.wasm")
    return folder


@pytest.fixture
def source_repository():
    """Return a factory for real local repositories with one source commit."""

    def create(directory):
        directory.mkdir(parents=True, exist_ok=True)
        build.git(directory, "init")
        build.git(directory, "config", "user.name", "Build fixture")
        build.git(directory, "config", "user.email", "fixture@example.invalid")
        (directory / "code.py").write_text("VALUE = 1\n")
        build.git(directory, "add", "code.py")
        build.git(directory, "commit", "-m", "Synthetic source fixture")
        return build.git(directory, "rev-parse", "HEAD")

    return create
