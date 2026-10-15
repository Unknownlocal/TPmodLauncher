# SPDX-License-Identifier: MPL-2.0
"""Validate inert source inputs, portable package layout and filesystem boundaries."""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess
import sys

import pytest

from launcher.mod_contract import PYTHON_LIMITS
from launcher.runner_host import bind_operations
from launcher.sandbox import Sandbox, SandboxError
from mod_runtime.bundle import EXPORTS
from mod_runtime import source
from mod_runtime.source import SourceError, SourceManifest, SourceSnapshot


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


@pytest.mark.parametrize("old,new", [
    (b"schema = 2", b"schema = 1"),
    (b"schema = 2", b"schema = true"),
    (b'schema = 2', b'schema = 2\ncomponent = "mod.wasm"'),
    (b'entry = "mod"', b'component = "mod.wasm"'),
    (b'"owned.source"', b'"../outside"'),
    (b'"0.1.0"', b'"01.0.0"'),
    (b'"0.1.0"', b'"1.0"'),
    (b'"0.1.0"', b'"1.0.0-01"'),
    (b'api = "0.2.0"', b'api = "0.1.0"'),
    (b'api = "0.2.0"', b'api = ["0.2.0"]'),
    (b'"python"', b'"wasm"'),
    (b'entry = "mod"', b'entry = "../outside"'),
    (b'entry = "mod"', b'entry = "src/mod.py"'),
    (b'entry = "mod"', b'entry = ""'),
    (b'entry = "mod"', b'entry = "pkg..mod"'),
    (b'entry = "mod"', b'entry = "class"'),
    (b'["diagnostics"]', b'["invented"]'),
    (b'["diagnostics"]', b'["diagnostics", "diagnostics"]'),
    (b'["diagnostics"]', b'"diagnostics"'),
    (b'["diagnostics"]', b'[true]'),
], ids=["old-schema", "bool-schema", "extra-field", "component-fallback", "id", "version-zero", "version-short", "prerelease-zero", "old-api", "api-type", "runtime", "entry-traversal", "entry-file", "entry-empty", "entry-gap", "entry-keyword", "capability", "duplicate-grant", "grant-type", "grant-value"])
def test_source_manifest_rejects_unsupported_metadata(old, new):
    with pytest.raises(SourceError):
        SourceManifest.parse(MANIFEST.replace(old, new))


@pytest.mark.parametrize("data", [b"\xff", b"[", b"schema = 2\nschema = 2", b"#" * (16 * 1024 + 1), b"x=" + b"[" * 2000], ids=["utf8", "toml", "duplicate", "size", "nesting"])
def test_invalid_or_over_budget_manifest(data):
    with pytest.raises(SourceError):
        SourceManifest.parse(data)


@pytest.mark.parametrize("version", ["0.1.0", "1.2.3-beta.1+build.2"])
def test_source_metadata_and_snapshot_are_immutable_and_do_not_execute(mod, version):
    (mod / "mod.toml").write_bytes(MANIFEST.replace(b"0.1.0", version.encode()))
    # Root-side payloads are never read, used as a fallback, or executed.
    (mod / "mod.wasm").write_bytes(b"not a component")
    original = (mod / "src/mod.py").read_bytes().decode("utf-8")
    snapshot = SourceSnapshot.read(mod)
    assert snapshot.manifest.id == "owned.source" and snapshot.manifest.version == version
    assert snapshot.manifest.capabilities == frozenset({"diagnostics"})
    assert snapshot.files == (("mod.py", original),)
    assert snapshot.total_bytes == len(original.encode("utf-8"))
    (mod / "src/mod.py").write_text("changed", encoding="utf-8")
    (mod / "mod.toml").write_text("changed", encoding="utf-8")
    assert snapshot.files == (("mod.py", original),)
    with pytest.raises(FrozenInstanceError):
        snapshot.total_bytes = 0
    with pytest.raises(FrozenInstanceError):
        snapshot.manifest.entry = "other"


def test_dotted_entry_explicit_packages_bom_and_sorted_paths(mod):
    (mod / "mod.toml").write_bytes(MANIFEST.replace(b'entry = "mod"', b'entry = "pkg.entry"'))
    (mod / "src/pkg").mkdir()
    (mod / "src/pkg/__init__.py").write_bytes(b"")
    (mod / "src/pkg/entry.py").write_bytes(b"\xef\xbb\xbf# caf\xc3\xa9\n")
    snapshot = SourceSnapshot.read(mod)
    assert snapshot.files == tuple(sorted(snapshot.files))
    assert dict(snapshot.files)["pkg/entry.py"] == "# caf\xe9\n"
    assert snapshot.total_bytes == sum(item.stat().st_size for item in (mod / "src").rglob("*.py"))


@pytest.mark.parametrize("host_modules", [frozenset(), frozenset({"mod", "future_host_only"})])
def test_snapshot_does_not_use_host_runtime_module_names(mod, monkeypatch, host_modules):
    monkeypatch.setattr(sys, "stdlib_module_names", host_modules)
    (mod / "src/future_host_only.py").write_bytes(b"")
    assert "future_host_only.py" in dict(SourceSnapshot.read(mod).files)
    # Snapshotting remains inert; runtime-name admission happens in the guest.
    (mod / "src/annotationlib.py").write_bytes(b"")
    assert "annotationlib.py" in dict(SourceSnapshot.read(mod).files)


@pytest.mark.parametrize("mutation", ["missing-entry", "missing-src", "invalid-utf8", "non-python", "package-init", "module-package", "invalid-name", "device-name"])
def test_unsupported_source_layouts(mod, mutation):
    if mutation == "missing-entry":
        (mod / "src/mod.py").unlink()
    elif mutation == "missing-src":
        (mod / "src/mod.py").unlink()
        (mod / "src").rmdir()
    elif mutation == "invalid-utf8":
        (mod / "src/mod.py").write_bytes(b"\xff")
    elif mutation == "non-python":
        (mod / "src/native.wasm").write_bytes(b"not source")
    elif mutation in ("package-init", "module-package"):
        (mod / "src/helpers").mkdir()
        (mod / "src/helpers/entry.py").write_bytes(b"")
        if mutation == "module-package":
            (mod / "src/helpers.py").write_bytes(b"")
            (mod / "src/helpers/__init__.py").write_bytes(b"")
    elif mutation == "invalid-name":
        (mod / "src/not-a-module.py").write_bytes(b"")
    else:
        # Validate the portable identifier directly: Windows cannot create CON.py.
        with pytest.raises(SourceError):
            SourceManifest.parse(MANIFEST.replace(b'entry = "mod"', b'entry = "CON"'))
        return
    with pytest.raises(SourceError):
        SourceSnapshot.read(mod)


@pytest.mark.parametrize("budget", ["bytes", "files", "entries", "depth", "length"])
def test_source_budgets_bound_work(mod, monkeypatch, budget):
    if budget == "bytes":
        (mod / "src/mod.py").write_bytes(b"#" * (source.MAX_SOURCE_BYTES + 1))
    elif budget == "files":
        monkeypatch.setattr(source, "MAX_SOURCE_FILES", 1)
        (mod / "src/other.py").write_bytes(b"")
    elif budget == "entries":
        monkeypatch.setattr(source, "MAX_TREE_ENTRIES", 1)
        (mod / "src/other").mkdir()
    elif budget == "depth":
        monkeypatch.setattr(source, "MAX_DEPTH", 1)
        (mod / "src/other").mkdir()
        (mod / "src/other/__init__.py").write_bytes(b"")
    else:
        (mod / "src" / ("a" * 127)).mkdir()
        (mod / "src" / ("a" * 127) / ("b" * 126 + ".py")).write_bytes(b"")
    with pytest.raises(SourceError, match="limit"):
        SourceSnapshot.read(mod)


def test_exact_byte_and_file_limits_accept_empty_helpers(mod, monkeypatch):
    (mod / "src/mod.py").write_bytes(b"#" * source.MAX_SOURCE_BYTES)
    (mod / "src/empty.py").write_bytes(b"")
    monkeypatch.setattr(source, "MAX_SOURCE_FILES", 2)
    assert SourceSnapshot.read(mod).total_bytes == source.MAX_SOURCE_BYTES


def test_byte_budget_is_aggregate_across_files(mod):
    (mod / "src/mod.py").write_bytes(b"#" * (source.MAX_SOURCE_BYTES // 2))
    (mod / "src/other.py").write_bytes(b"#" * (source.MAX_SOURCE_BYTES // 2 + 1))
    with pytest.raises(SourceError, match="byte limit"):
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


def test_observed_write_during_read_is_rejected_and_handles_are_closed(mod, monkeypatch):
    original = os.read
    attempted = []
    def mutate(descriptor, maximum):
        content = original(descriptor, maximum)
        if content.startswith(b"raise AssertionError") and not attempted:
            attempted.append(True)
            # Windows denies this write while its file handle is pinned; POSIX
            # permits it and the before/after metadata/read-size checks reject it.
            with (mod / "src/mod.py").open("ab") as stream:
                stream.write(b"# observed change")
        return content
    monkeypatch.setattr(source.disk.os, "read", mutate)
    with pytest.raises(SourceError):
        SourceSnapshot.read(mod)
    assert attempted == [True]
    monkeypatch.setattr(source.disk.os, "read", original)
    assert SourceSnapshot.read(mod).files


def test_pinned_root_cannot_redirect_reads_after_replacement(mod, tmp_path, monkeypatch):
    original = source.disk.read_file
    saved = mod.with_name("saved original")
    outside = tmp_path / "outside"
    (outside / "src").mkdir(parents=True)
    (outside / "mod.toml").write_bytes(MANIFEST)
    (outside / "src/mod.py").write_bytes(b"# outside content")
    changed = []
    def redirect(path, parent, maximum, expected=None):
        if path.name == "mod.toml" and not changed:
            changed.append(True)
            mod.rename(saved)
            mod.symlink_to(outside, target_is_directory=True)
        return original(path, parent, maximum, expected)
    monkeypatch.setattr(source.disk, "read_file", redirect)
    try:
        if os.name == "nt":
            with pytest.raises(SourceError):
                SourceSnapshot.read(mod)
            assert mod.is_dir() and not saved.exists()
        else:
            snapshot = SourceSnapshot.read(mod)
            assert "Host must not execute" in dict(snapshot.files)["mod.py"]
        assert changed == [True]
    finally:
        if mod.is_symlink():
            mod.unlink()


@pytest.mark.parametrize("target", ["mod.toml", "src/mod.py"])
def test_hardlinked_inputs_are_rejected(mod, tmp_path, target):
    os.link(mod / target, tmp_path / "second-name")
    with pytest.raises(SourceError, match="unshared"):
        SourceSnapshot.read(mod)


@pytest.mark.parametrize("target", ["mod.toml", "src/mod.py"])
def test_special_files_cannot_block_or_supply_source(mod, target):
    path = mod / target
    path.unlink()
    if os.name == "posix":
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(SourceError):
        SourceSnapshot.read(mod)


@pytest.mark.parametrize("target", ["root", "src", "nested"])
def test_linked_directories_and_ancestors_are_rejected(mod, tmp_path, target):
    link = tmp_path / "linked"
    if target == "root":
        destination = mod.parent
        requested = link / mod.name
    elif target == "src":
        (mod / "src").rename(mod / "saved")
        link, destination, requested = mod / "src", mod / "saved", mod
    else:
        link, destination, requested = mod / "src/linked", tmp_path, mod
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(destination)], check=True, capture_output=True)
    else:
        link.symlink_to(destination, target_is_directory=True)
    try:
        with pytest.raises(SourceError):
            SourceSnapshot.read(requested)
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


@pytest.mark.skipif(os.name == "nt", reason="Case-sensitive paths and symlinks use the POSIX branch")
@pytest.mark.parametrize("mutation", ["file-link", "case-file", "case-package"])
def test_posix_links_and_portability_collisions(mod, tmp_path, mutation):
    if mutation == "file-link":
        (mod / "src/mod.py").unlink()
        outside = tmp_path / "outside.py"
        outside.write_bytes(b"not permitted")
        (mod / "src/mod.py").symlink_to(outside)
    elif mutation == "case-file":
        (mod / "src/MOD.py").write_bytes(b"")
    else:
        (mod / "src/helpers.py").write_bytes(b"")
        (mod / "src/Helpers").mkdir()
        (mod / "src/Helpers/__init__.py").write_bytes(b"")
    with pytest.raises(SourceError):
        SourceSnapshot.read(mod)


def test_snapshot_needs_only_stdlib_in_isolated_process(mod, tmp_path):
    root = Path(__file__).resolve().parents[2]
    script = '''import importlib.util, sys
from pathlib import Path
root=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location("mod_runtime",root/"mod_runtime/__init__.py",submodule_search_locations=[str(root/"mod_runtime")])
module=importlib.util.module_from_spec(spec)
sys.modules["mod_runtime"]=module
spec.loader.exec_module(module)
from mod_runtime.source import SourceSnapshot
assert SourceSnapshot.read(sys.argv[2]).files
assert not any(name.split(".")[0] in {"launcher","hook_manager","wasmtime","componentize_py"} for name in sys.modules)
'''
    subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root), str(mod)], cwd=tmp_path, check=True, timeout=30)


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


def test_guest_captures_its_namespace_before_author_loading(runner_bundle):
    code = '''import sys, json, runner_guest
class WitWorld:
    def activate(self): pass
    def on_event(self, name, payload):
        roots = {item.split(".")[0].casefold() for item in sys.modules}
        roots.update(item.casefold() for item in sys.stdlib_module_names)
        return json.dumps(sorted(roots - runner_guest._reserved))
'''
    with Sandbox(runner_bundle.component(), bind_operations(set(), {}),
                 limits=PYTHON_LIMITS, required_exports=dict(EXPORTS)) as guest:
        guest.call("put-source", "mod.py", code)
        assert guest.call("load-source", "mod") == ""
        guest.call("activate")
        # The author module was added after the immutable runtime snapshot.
        assert guest.call("on-event", "probe", "{}") == '["mod"]'


@pytest.mark.parametrize("path", [
    "annotationlib.py", "json.py", "JSON.py", "json/__init__.py",
    "runner_bindings.py", "runner_guest.py", "wit_world.py",
    "componentize_py_runtime.py", "_source_policy.py", "RUNNER_BINDINGS.py",
])
def test_guest_rejects_runtime_collisions_during_transfer_before_author_execution(mod, runner_bundle, path):
    target = mod / "src" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'import wit_world\nwit_world.report("must not execute")\n')
    snapshot = SourceSnapshot.read(mod)
    messages, cleaned = [], []
    with Sandbox(runner_bundle.component(), bind_operations({"diagnostics"}, {"report": messages.append}),
                 limits=PYTHON_LIMITS, required_exports=dict(EXPORTS), cleanup=lambda: cleaned.append(True)) as guest:
        text = dict(snapshot.files)[path]
        with pytest.raises(SandboxError) as rejected:
            guest.call("put-source", path, text)
        assert not any(word in str(rejected.value).lower() for word in ("fuel", "deadline", "interrupt"))
        assert guest.closed and cleaned == [True] and messages == []
    assert cleaned == [True]
