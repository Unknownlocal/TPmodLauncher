# SPDX-License-Identifier: MPL-2.0
"""Real trusted-runner loading, aggregate source budgets and failure ownership."""

from dataclasses import replace

import pytest

from launcher.sandbox import SandboxError
from mod_runtime import shared_sources
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest, SourceSnapshot


MANIFEST = SourceManifest.parse(b'''schema = 2
id = "owned.runner"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics"]
''')
CODE = '''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self): wit_world.report("ready")
    def on_event(self, name, payload): return payload
'''


def snapshot(code=CODE, **extra):
    files = tuple(sorted({"mod.py": code, **extra}.items()))
    return SourceSnapshot(MANIFEST, files, sum(len(text.encode("utf-8")) for _, text in files))


def test_multibyte_source_crosses_chunk_boundaries_and_dotted_entry_loads(runner_bundle):
    text = "#" + "\u00e9\u6c34\U0001f3ce" * 1000 + "\n" + CODE
    data = snapshot("", **{"pkg/__init__.py": "", "pkg/entry.py": text})
    data = replace(data, manifest=replace(MANIFEST, entry="pkg.entry"))
    messages = []
    with SharedPythonRunner(runner_bundle, (data,), {MANIFEST.id: {"report": messages.append}}) as runner:
        runner.activate(MANIFEST.id)
    assert messages == ["ready"]


def test_source_budget_stops_import_loop_and_cleans(runner_bundle, monkeypatch):
    monkeypatch.setattr(shared_sources, "SOURCE_FUEL", 5_000_000)
    cleaned = []
    with pytest.raises(SandboxError, match="fuel"):
        SharedPythonRunner(runner_bundle, (snapshot("while True: pass"),), {MANIFEST.id: {"report": lambda _: None}}, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]
