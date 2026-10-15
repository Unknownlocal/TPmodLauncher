# SPDX-License-Identifier: MPL-2.0
"""Real Git source fixtures shared by launcher build and source-package tests."""

from pathlib import Path
import shutil

import pytest

from launcher import build_frida as build
from launcher import build_mod
from wasmtime import wat2wasm


@pytest.fixture
def code_mod():
    """Create a small owned mod with the real manifest and API; no Python build is needed."""
    root = Path(__file__).resolve().parents[2]

    def create(pack, name, *, mod_id=None, trap=False):
        folder = pack / name
        folder.mkdir(parents=True)
        manifest = (root / "examples/diagnostic_mod/mod.toml").read_text()
        (folder / "mod.toml").write_text(
            manifest.replace("example.diagnostics", mod_id or name).replace(
                'runtime = "python"', 'runtime = "wasm"'
            ),
            encoding="utf-8",
        )
        source = (Path(__file__).with_name("fixtures") / "contract.wat").read_text()
        source = source.replace("@RESULT@", "{}").replace("@LENGTH@", "2")
        if trap:
            source = source.replace(
                '(func (export "activate"))', '(func (export "activate") unreachable)'
            )
        (folder / "mod.wasm").write_bytes(wat2wasm(source))
        return folder

    return create


@pytest.fixture(scope="session")
def built_python_mod(tmp_path_factory):
    """Build one owned Python component for both normal and shipped-interpreter checks."""
    root = Path(__file__).resolve().parents[2]
    folder = tmp_path_factory.mktemp("owned-python-contract")
    shutil.copyfile(root / "examples/diagnostic_mod/mod.toml", folder / "mod.toml")
    shutil.copytree(
        Path(__file__).with_name("fixtures") / "contract_mod", folder / "src"
    )
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("MODLAUNCHER_TEST_SECRET", "build-secret-not-for-guests")
        build_mod.build(folder, folder / "mod.wasm")
    return folder


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
