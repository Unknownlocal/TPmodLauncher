# SPDX-License-Identifier: MPL-2.0
"""Bundle integrity, source boundaries and real trusted-runner acceptance."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from wasmtime import wat2wasm

from mod_runtime import ABI_VERSION, BundleError, RunnerBundle
from mod_runtime import bundle as artifacts
from mod_runtime import build as builder
from launcher.mod_contract import PYTHON_LIMITS
from launcher.runner_host import bind_operations
from launcher.sandbox import Sandbox

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def synthetic(tmp_path):
    """Small owned inventory; real Wasm ABI/build behaviour is tested separately."""
    directory = tmp_path / "owned bundle"
    records = {}
    for name in artifacts.FILES:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = wat2wasm("(component)") if name.endswith(".wasm") else b"Owned synthetic fixture, not upstream licence content.\n"
        path.write_bytes(data)
        records[name] = dict(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    receipt = dict(format=1, abi=ABI_VERSION, componentize_py=artifacts.COMPONENTIZE_VERSION, files=records)
    (directory / artifacts.MANIFEST).write_text(json.dumps(receipt), encoding="utf-8")
    return directory, receipt


def test_runner_returns_verified_bytes_and_relocates(synthetic, tmp_path):
    directory, _ = synthetic
    copied = tmp_path / "relocated \U0001f3ce bundle"
    shutil.copytree(directory, copied)
    bundle = RunnerBundle(copied)
    assert bundle.component() == (copied / "runner.wasm").read_bytes()


@pytest.mark.parametrize("target", ["runner.wasm", "source/guest.py", "notices/LICENSE.PythonGuest.txt"])
@pytest.mark.parametrize("operation", ["modify", "remove"])
def test_changed_or_missing_payload_source_and_notice_fail(synthetic, target, operation):
    directory, _ = synthetic
    path = directory / target
    if operation == "remove":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(BundleError):
        RunnerBundle(directory)


def test_component_changed_after_open_is_checked_again(synthetic):
    directory, _ = synthetic
    bundle = RunnerBundle(directory)
    (directory / "runner.wasm").write_bytes(b"changed")
    with pytest.raises(BundleError, match="receipt|size"):
        bundle.component()


@pytest.mark.parametrize("extra", ["unexpected.txt", "runners/extra.wasm", "extra/nested.txt", "source/__pycache__/extra.pyc"])
def test_uninventoried_files_and_directories_are_rejected(synthetic, extra):
    directory, _ = synthetic
    path = directory / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unexpected")
    with pytest.raises(BundleError, match="Unexpected"):
        RunnerBundle(directory)


@pytest.mark.parametrize("mutation", ["format", "abi", "toolchain", "missing", "traversal", "digest", "size", "oversized"])
def test_unsupported_or_malformed_receipts_are_rejected(synthetic, mutation):
    directory, receipt = synthetic
    if mutation == "format":
        receipt["format"] = True
    elif mutation == "abi":
        receipt["abi"] = "99.0.0"
    elif mutation == "toolchain":
        receipt["componentize_py"] = "0.0.0"
    elif mutation == "missing":
        receipt["files"].pop("source/guest.py")
    elif mutation == "traversal":
        receipt["files"]["../escape"] = receipt["files"].pop("source/guest.py")
    elif mutation == "digest":
        receipt["files"]["source/guest.py"]["sha256"] = "invalid"
    elif mutation == "size":
        receipt["files"]["source/guest.py"]["size"] = True
    else:
        receipt["files"]["source/guest.py"]["size"] = artifacts.MAX_COMPONENT_BYTES + 1
    (directory / artifacts.MANIFEST).write_text(json.dumps(receipt))
    with pytest.raises(BundleError):
        RunnerBundle(directory)


@pytest.mark.parametrize("data", [b'{"format":1,"format":1}', b"[", b"\xff", b" " * (64 * 1024 + 1), b"[" * 2000, b'{"format":' + b"1" * 5000 + b'}'], ids=["duplicate", "invalid-json", "invalid-utf8", "oversized", "nested", "integer-limit"])
def test_duplicate_invalid_or_oversized_manifest(synthetic, data):
    directory, _ = synthetic
    (directory / artifacts.MANIFEST).write_bytes(data)
    with pytest.raises(BundleError):
        RunnerBundle(directory)


def test_hardlinked_file_is_rejected(synthetic, tmp_path):
    directory, _ = synthetic
    original = directory / "source/guest.py"
    os.link(original, tmp_path / "second-name.py")
    with pytest.raises(BundleError, match="unshared"):
        RunnerBundle(directory)


def test_linked_bundle_parent_is_rejected(synthetic, tmp_path):
    directory, _ = synthetic
    link = tmp_path / "linked-parent"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(directory.parent)], check=True, capture_output=True)
    else:
        link.symlink_to(directory.parent, target_is_directory=True)
    try:
        with pytest.raises(BundleError, match="linked"):
            RunnerBundle(link / directory.name)
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_builder_failure_does_not_publish_or_replace_output(tmp_path, monkeypatch):
    target = tmp_path / "failed-build"
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(builder.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        builder.build(target)
    assert not target.exists()
    target.mkdir()
    marker = target / "existing"
    marker.write_text("preserve")
    with pytest.raises(FileExistsError):
        builder.build(target)
    assert marker.read_text() == "preserve"


def test_package_import_needs_no_compiler_wasmtime_or_launcher(tmp_path):
    # A clean isolated process has no repository path except this explicit trusted package.
    script = '''import importlib.util, sys
from pathlib import Path
root=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location("mod_runtime",root/"mod_runtime/__init__.py",submodule_search_locations=[str(root/"mod_runtime")])
module=importlib.util.module_from_spec(spec)
sys.modules["mod_runtime"]=module
spec.loader.exec_module(module)
assert not any(name.split(".")[0] in {"launcher","hook_manager","wasmtime","componentize_py"} for name in sys.modules)
'''
    subprocess.run([sys.executable, "-I", "-B", "-c", script, str(ROOT)], cwd=tmp_path, check=True, timeout=20)


def test_real_bundle_retains_exact_source_and_upstream_texts(runner_bundle):
    assert (runner_bundle.directory / "source/guest.py").read_bytes() == (ROOT / "mod_runtime/guest.py").read_bytes()
    assert (runner_bundle.directory / "source/_source_policy.py").read_bytes() == (ROOT / "mod_runtime/_source_policy.py").read_bytes()
    assert (runner_bundle.directory / "source/runner.wit").read_bytes() == (ROOT / "mod_runtime" / ABI_VERSION / "runner.wit").read_bytes()
    for name in artifacts.NOTICE_FILES:
        origin = ROOT / "LICENCE" if name.endswith("LICENCE.Project.txt") else ROOT / "notices/mod-runtime" / Path(name).name
        assert (runner_bundle.directory / name).read_bytes() == origin.read_bytes()


def test_real_runner_abi_is_exact(runner_bundle, monkeypatch):
    data = runner_bundle.component()
    builder.check_interface(data)
    # The runner imports the complete interface, even if the host registry drifts.
    monkeypatch.setattr(builder, "HOST_OPERATIONS", {"report": builder.HOST_OPERATIONS["report"]})
    with pytest.raises(BundleError, match="imports/exports"):
        builder.check_interface(data)


@pytest.mark.parametrize("example", ["diagnostic_mod", "menu_mod"])
def test_built_runner_loads_existing_example_with_unchanged_sandbox(runner_bundle, example):
    messages, buttons = [], []
    capabilities = {"diagnostics"} if example == "diagnostic_mod" else {"diagnostics", "main-menu"}
    operations = bind_operations(capabilities, {"report": messages.append, "add-menu-button": lambda *values: buttons.append(values)})
    with Sandbox(runner_bundle.component(), operations, limits=PYTHON_LIMITS, required_exports=dict(artifacts.EXPORTS)) as guest:
        source = (ROOT / "examples" / example / "src/mod.py").read_text(encoding="utf-8")
        guest.call("put-source", "mod.py", source)
        assert guest.call("load-source", "mod") == ""
        guest.call("activate")
        assert json.loads(guest.call("on-event", "probe", '{"owned":true}')) == ({"event": "probe", "received": {"owned": True}} if example == "diagnostic_mod" else None)
    assert len(messages) == 1
    assert len(buttons) == (1 if "main-menu" in capabilities else 0)


def test_interface_check_rejects_unexpected_wasm(runner_bundle):
    with pytest.raises(BundleError):
        builder.check_interface(wat2wasm("(component)"))


def test_interface_check_rejects_extra_nonfunction_export(runner_bundle):
    # Valid component export section: expose existing core module 0 as "extra".
    # All expected imports/functions remain, so filtering nonfunctions misses it.
    extra_export = bytes([11, 12, 1, 0, 5, 101, 120, 116, 114, 97, 0, 17, 0, 0])
    data = runner_bundle.component() + extra_export
    with pytest.raises(BundleError, match="function type"):
        builder.check_interface(data)


@pytest.mark.embedded
def test_relocated_player_verifies_and_runs_bundle_without_compiler(embedded_project, runner_bundle):
    root = embedded_project
    shutil.copytree(runner_bundle.directory, root / "libs/mod-runners")
    probe = root / "launcher/runner_probe.py"
    shutil.copyfile(Path(__file__).with_name("fixtures") / "embedded_runner_probe.py", probe)
    owned_mod = root / ".state/runner-game/data/owned pack/owned.source"
    owned_mod.mkdir(parents=True)
    linked = root / ".state/runner-linked"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(owned_mod)], check=True, capture_output=True)
    try:
        result = subprocess.run([str(root / "libs/python-3.13.16-embed-amd64/python.exe"), "-I", "-B", str(probe)], capture_output=True, text=True, timeout=30)
    finally:
        linked.rmdir()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "without componentize-py" in result.stdout
