# SPDX-License-Identifier: MPL-2.0
"""Real trusted-runner loading, aggregate source budgets and failure ownership."""

from dataclasses import replace

import pytest

from launcher.sandbox import SandboxError
from mod_runtime import runtime
from mod_runtime.runtime import PythonRunner
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
    with PythonRunner(runner_bundle, data, {"report": messages.append}) as runner:
        runner.activate()
    assert messages == ["ready"]


@pytest.mark.parametrize("code,pattern", [
    ("def broken(:\n", "SyntaxError"),
    ('raise ValueError("owned import failure")', "owned import failure"),
], ids=["syntax", "import"])
def test_source_load_errors_close_and_clean_before_returning(runner_bundle, code, pattern):
    cleaned = []
    with pytest.raises(SandboxError, match=pattern):
        PythonRunner(runner_bundle, snapshot(code), {"report": lambda _: None}, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


def test_source_budget_stops_import_loop_and_cleans(runner_bundle, monkeypatch):
    monkeypatch.setattr(runtime, "SOURCE_FUEL", 5_000_000)
    cleaned = []
    with pytest.raises(SandboxError, match="fuel"):
        PythonRunner(runner_bundle, snapshot("while True: pass"), {"report": lambda _: None}, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


def test_callback_loops_keep_normal_budget_and_fail_independently(runner_bundle):
    code = CODE.replace("return payload", "exec('while True: pass')")
    cleaned = []
    with PythonRunner(runner_bundle, snapshot(code), {"report": lambda _: None}, cleanup=lambda: cleaned.append(True)) as failed, PythonRunner(
        runner_bundle, snapshot(), {"report": lambda _: None}
    ) as healthy:
        with pytest.raises(SandboxError, match="fuel"):
            failed.activate()
            failed.on_event("probe", "{}")
        assert failed.closed and cleaned == [True]
        healthy.activate()
        assert healthy.on_event("probe", "{}") == "{}"
