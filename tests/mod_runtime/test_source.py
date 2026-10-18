# SPDX-License-Identifier: MPL-2.0
"""Validate inert source inputs, portable package layout and filesystem boundaries."""

from contextlib import contextmanager
import os
import subprocess

import pytest

from launcher.mod_contract import PYTHON_LIMITS
from launcher.runner_host import bind_operations
from launcher.sandbox import Sandbox, SandboxError
from mod_runtime.bundle import EXPORTS
from mod_runtime import source
from mod_runtime.source import SourceError, SourceSnapshot


MANIFEST = b'''schema = 2
id = "owned.source"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics"]
'''


@pytest.fixture
def mod(tmp_path):
    root = tmp_path / "owned mod"
    (root / "src").mkdir(parents=True)
    (root / "mod.toml").write_bytes(MANIFEST)
    (root / "src/mod.py").write_text('raise AssertionError("Host must not execute this")\n', encoding="utf-8")
    return root


def test_source_metadata_and_snapshot_are_immutable_and_do_not_execute(mod):
    # Root-side payloads are never read, used as a fallback, or executed.
    (mod / "mod.wasm").write_bytes(b"not a component")
    original = (mod / "src/mod.py").read_bytes().decode("utf-8")
    snapshot = SourceSnapshot.read(mod)
    assert snapshot.manifest.id == "owned.source" and snapshot.manifest.version == "0.1.0"
    assert snapshot.manifest.capabilities == frozenset({"diagnostics"})
    assert snapshot.files == (("mod.py", original),)
    assert snapshot.total_bytes == len(original.encode("utf-8"))
    (mod / "src/mod.py").write_text("changed", encoding="utf-8")
    (mod / "mod.toml").write_text("changed", encoding="utf-8")
    assert snapshot.files == (("mod.py", original),)


@pytest.mark.parametrize("budget", ["bytes", "files"])
def test_source_budgets_bound_work(mod, monkeypatch, budget):
    if budget == "bytes":
        (mod / "src/mod.py").write_bytes(b"#" * (source.MAX_SOURCE_BYTES + 1))
    else:
        monkeypatch.setattr(source, "MAX_SOURCE_FILES", 1)
        (mod / "src/other.py").write_bytes(b"")
    with pytest.raises(SourceError, match="limit"):
        SourceSnapshot.read(mod)


def test_file_replacement_between_scan_and_open_is_rejected(mod, monkeypatch):
    original = source.disk.opened
    @contextmanager
    def replace(path, parent=None, *, directory=False):
        if path.name == "mod.py":
            path.rename(mod / "saved.py")
            path.write_bytes(b"# replaced after enumeration")
        with original(path, parent, directory=directory) as descriptor:
            yield descriptor
    monkeypatch.setattr(source.disk, "opened", replace)
    with pytest.raises(SourceError):
        SourceSnapshot.read(mod)


def test_linked_ancestor_is_rejected(mod, tmp_path):
    link = tmp_path / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(mod.parent)], check=True, capture_output=True)
    else:
        link.symlink_to(mod.parent, target_is_directory=True)
    try:
        with pytest.raises(SourceError):
            SourceSnapshot.read(link / mod.name)
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_snapshot_loads_packages_in_real_runner_after_disk_changes(mod, runner_bundle):
    (mod / "src/helpers").mkdir()
    (mod / "src/helpers/__init__.py").write_bytes(b'VALUE = "original snapshot"\n')
    (mod / "src/mod.py").write_bytes(b'''from helpers import VALUE
import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self): wit_world.report(VALUE)
    def on_event(self, name, payload): return payload
''')
    snapshot = SourceSnapshot.read(mod)
    (mod / "src/helpers/__init__.py").write_bytes(b'raise AssertionError("changed after snapshot")')
    messages = []
    with Sandbox(runner_bundle.component(), bind_operations(snapshot.manifest.capabilities, {"report": messages.append}),
                 limits=PYTHON_LIMITS, required_exports=dict(EXPORTS)) as guest:
        for name, text in snapshot.files:
            guest.call("put-source", name, text)
        assert guest.call("load-source", snapshot.manifest.entry) == ""
        guest.call("activate")
        assert guest.call("on-event", "probe", "{}") == "{}"
    assert messages == ["original snapshot"]


def test_guest_rejects_runtime_collisions_during_transfer_before_author_execution(mod, runner_bundle):
    (mod / "src/json.py").write_bytes(b'import wit_world\nwit_world.report("must not execute")\n')
    snapshot = SourceSnapshot.read(mod)
    messages, cleaned = [], []
    with Sandbox(runner_bundle.component(), bind_operations({"diagnostics"}, {"report": messages.append}),
                 limits=PYTHON_LIMITS, required_exports=dict(EXPORTS), cleanup=lambda: cleaned.append(True)) as guest:
        text = dict(snapshot.files)["json.py"]
        with pytest.raises(SandboxError) as rejected:
            guest.call("put-source", "json.py", text)
        assert not any(word in str(rejected.value).lower() for word in ("fuel", "deadline", "interrupt"))
        assert guest.closed and cleaned == [True] and messages == []
    assert cleaned == [True]
