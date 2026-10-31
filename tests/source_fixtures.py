# SPDX-License-Identifier: MPL-2.0
"""Source and small component fixtures shared by component and integration suites."""

from pathlib import Path

import pytest
from wasmtime import wat2wasm
from tests.source_support import write_source_mod


@pytest.fixture
def source_mod():
    """Create source-only Python inputs; stale author Wasm is deliberately unusable."""
    def create(pack, name, *, mod_id=None, code=None, capabilities=("diagnostics",), api="0.2.0"):
        if code is None:
            code = '''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self): wit_world.report("source ready")
    def on_event(self, name, payload): return payload
'''
        folder = write_source_mod(pack / name, code, owner=mod_id or name, capabilities=capabilities, api=api)
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
        source = (Path(__file__).parent / "launcher/fixtures" / "contract.wat").read_text()
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
