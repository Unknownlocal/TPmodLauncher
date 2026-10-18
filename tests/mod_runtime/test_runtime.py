# SPDX-License-Identifier: MPL-2.0
"""Real trusted-runner loading, aggregate source budgets and failure ownership."""

from dataclasses import replace

import pytest

from launcher.compiled_mod import CompiledMod
from launcher.sandbox import SandboxError
from mod_runtime import runtime
from mod_runtime.runtime import PythonRunner
from mod_runtime.source import MAX_SOURCE_BYTES, SourceManifest, SourceSnapshot


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


def test_chunks_preserve_utf8_and_bounded_plain_strings():
    for text in ("", "a" * 10000, "a" * 4095 + "\u00e9\u6c34\U0001f3ce" * 2000):
        chunks = list(runtime._chunks(text.encode("utf-8")))
        assert chunks and "".join(chunks) == text
        assert all(len(chunk.encode("utf-8")) <= 4096 for chunk in chunks)


def test_large_source_and_empty_helpers_load_with_utf8_transfer(runner_bundle):
    # A comment-heavy source legitimately needs more than a normal callback's fuel.
    text = CODE + "#" + "a" * (MAX_SOURCE_BYTES - len(CODE) - 1)
    messages = []
    with PythonRunner(runner_bundle, snapshot(text, **{"empty.py": ""}), {"report": messages.append}) as runner:
        runner.activate()
        assert runner.on_event("probe", '{"ok":true}') == '{"ok":true}'
    assert messages == ["ready"]


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


def test_transfer_collision_cleans_before_author_code_runs(runner_bundle):
    messages, cleaned = [], []
    with pytest.raises(SandboxError):
        PythonRunner(runner_bundle, snapshot(**{"json.py": 'import wit_world; wit_world.report("bad")'}),
                     {"report": messages.append}, cleanup=lambda: cleaned.append(True))
    assert messages == [] and cleaned == [True]


def test_source_budget_stops_import_loop_and_cleans(runner_bundle, monkeypatch):
    monkeypatch.setattr(runtime, "SOURCE_FUEL", 5_000_000)
    cleaned = []
    with pytest.raises(SandboxError, match="fuel"):
        PythonRunner(runner_bundle, snapshot("while True: pass"), {"report": lambda _: None}, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


def test_source_phase_counts_transfer_calls_and_import_together(runner_bundle, monkeypatch):
    original = runtime._chunks
    def many(data):
        # Emit enough bounded empty chunks to use work across multiple guest entries.
        for _ in range(10):
            yield ""
        yield from original(data)
    monkeypatch.setattr(runtime, "_chunks", many)
    monkeypatch.setattr(runtime, "MAX_TRANSFER_CALLS", 5)
    cleaned = []
    with pytest.raises(SandboxError, match="transfer exceeds its call limit"):
        PythonRunner(runner_bundle, snapshot(), {"report": lambda _: None}, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


def test_source_deadline_includes_host_transfer_work(runner_bundle, monkeypatch):
    import time
    from launcher import sandbox
    original = runtime._chunks
    now = [time.monotonic()]
    def delayed(data):
        now[0] += runtime.SOURCE_SECONDS + 1
        yield from original(data)
    monkeypatch.setattr(sandbox.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(runtime, "_chunks", delayed)
    cleaned = []
    with pytest.raises(SandboxError, match="deadline"):
        PythonRunner(runner_bundle, snapshot(), {"report": lambda _: None}, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


@pytest.mark.parametrize("payload", ["file-count", "byte-count"])
def test_loader_enforces_aggregate_bounds_even_for_manually_constructed_snapshots(runner_bundle, payload):
    data = snapshot()
    if payload == "file-count":
        data = replace(data, files=tuple((f"module{index}.py", "") for index in range(257)))
    else:
        data = snapshot("#" * (MAX_SOURCE_BYTES // 2), **{"helper.py": "#" * (MAX_SOURCE_BYTES // 2 + 1)})
    cleaned = []
    with pytest.raises(SandboxError, match="limit"):
        PythonRunner(runner_bundle, data, {"report": lambda _: None}, cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


def test_pre_sandbox_failures_release_cleanup_and_compiler(runner_bundle):
    compiled = CompiledMod(runner_bundle.component())
    cleaned = []
    providers = {"report": lambda _: None}
    compiled.data = b"mismatched portable bytes"
    with pytest.raises((SandboxError, ValueError)):
        PythonRunner(runner_bundle, snapshot(), providers, prepared=compiled, cleanup=lambda: cleaned.append(True))
    assert compiled.closed and cleaned == [True]


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
