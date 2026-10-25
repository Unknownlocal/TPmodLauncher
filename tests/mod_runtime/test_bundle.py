# SPDX-License-Identifier: MPL-2.0
"""Bundle integrity, source boundaries and real trusted-runner acceptance."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from wasmtime import wat2wasm

from mod_runtime import ABI_VERSION, BundleError, RunnerBundle
from mod_runtime import bundle as artifacts

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
    bundle = RunnerBundle(directory).copy_to(copied)
    assert bundle.component() == (copied / "runner.wasm").read_bytes()


def test_component_changed_after_open_is_checked_again(synthetic):
    directory, _ = synthetic
    bundle = RunnerBundle(directory)
    (directory / "runner.wasm").write_bytes(b"changed")
    with pytest.raises(BundleError, match="receipt|size"):
        bundle.component()


def test_real_bundle_retains_exact_source_and_upstream_texts(runner_bundle):
    for name in artifacts.SOURCES:
        relative = Path(name).relative_to("source")
        origin = ROOT / "mod_runtime" / ABI_VERSION / relative if relative == Path("runner.wit") else ROOT / "mod_runtime" / relative
        assert (runner_bundle.directory / name).read_bytes() == origin.read_bytes()
    for name in artifacts.NOTICE_FILES:
        origin = ROOT / "LICENCE" if name.endswith("LICENCE.Project.txt") else ROOT / "notices/mod-runtime" / Path(name).name
        assert (runner_bundle.directory / name).read_bytes() == origin.read_bytes()


def test_windows_checkout_preserves_frozen_runner_source_bytes(tmp_path):
    # Model the Linux-built artifact's LF source in a separate repository; the
    # real checkout rules must preserve those bytes with Windows conversion on.
    shutil.copyfile(ROOT / ".gitattributes", tmp_path / ".gitattributes")
    sources = {}
    for name in artifacts.SOURCES:
        relative = Path(name).relative_to("source")
        origin = ROOT / "mod_runtime" / ABI_VERSION / relative if relative == Path("runner.wit") else ROOT / "mod_runtime" / relative
        path = origin.relative_to(ROOT)
        sources[path] = origin.read_bytes().replace(b"\r\n", b"\n")
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(sources[path])
    command = ["git", "-C", str(tmp_path), "-c", "core.autocrlf=true", "-c", "core.safecrlf=false"]
    for arguments in (("init", "-q"), ("add", ".")):
        subprocess.run([*command, *arguments], check=True, capture_output=True, timeout=10)
    for path in sources:
        (tmp_path / path).unlink()
    subprocess.run([*command, "checkout-index", "--all"], check=True, capture_output=True, timeout=10)
    for path, expected in sources.items():
        assert (tmp_path / path).read_bytes() == expected, path


@pytest.mark.embedded
def test_relocated_player_verifies_and_runs_bundle_without_compiler(embedded_project):
    root = embedded_project
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
