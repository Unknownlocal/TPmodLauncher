# SPDX-License-Identifier: MPL-2.0
"""Real Git source fixtures shared by launcher build and source-package tests."""

from pathlib import Path
import json
import shutil

import pytest

from launcher import build_frida as build
from launcher import build_mod
from wasmtime import wat2wasm


@pytest.fixture
def source_mod():
    """Create source-only Python inputs; stale author Wasm is deliberately unusable."""
    def create(pack, name, *, mod_id=None, code=None, capabilities=("diagnostics",)):
        folder = pack / name
        (folder / "src").mkdir(parents=True)
        (folder / "mod.toml").write_text(f'''schema = 2
id = "{mod_id or name}"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = {json.dumps(capabilities)}
''', encoding="utf-8")
        if code is None:
            code = '''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self): wit_world.report("source ready")
    def on_event(self, name, payload): return payload
'''
        (folder / "src/mod.py").write_text(code, encoding="utf-8")
        (folder / "mod.wasm").write_bytes(b"stale author component must not load")
        return folder
    return create


@pytest.fixture
def code_mod(legacy_diagnostic_manifest):
    """Create a small owned legacy component; no Python build is needed."""

    def create(pack, name, *, mod_id=None, trap=False):
        folder = pack / name
        folder.mkdir(parents=True)
        (folder / "mod.toml").write_text(
            legacy_diagnostic_manifest.replace("example.diagnostics", mod_id or name).replace(
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
def legacy_diagnostic_manifest():
    """Keep legacy author-component coverage independent of public source examples."""
    return '''schema = 1
id = "example.diagnostics"
version = "0.1.0"
api = "0.1.0"
runtime = "python"
component = "mod.wasm"
capabilities = ["diagnostics"]
'''


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
