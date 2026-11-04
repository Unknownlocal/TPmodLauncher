# SPDX-License-Identifier: MPL-2.0
"""Bundle integrity, source boundaries and real trusted-runner acceptance."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from wasmtime import wat2wasm

from launcher.prepare_release import runner_artifacts
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
    bundle = RunnerBundle(directory / ".." / directory.name).copy_to(copied)
    assert bundle.component() == (copied / "runner.wasm").read_bytes()
    # Release preparation loads the same reader directly, without its package.
    assert runner_artifacts().RunnerBundle(copied).component() == bundle.component()


def test_component_changed_after_open_is_checked_again(synthetic):
    directory, _ = synthetic
    bundle = RunnerBundle(directory)
    (directory / "runner.wasm").write_bytes(b"changed")
    with pytest.raises(BundleError, match="receipt|size"):
        bundle.component()


def directory_link(path, target):
    """Use real directory links, including junctions without Windows admin rights."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(path), str(target)],
                       check=True, capture_output=True, timeout=10)
    else:
        path.symlink_to(target, target_is_directory=True)


def remove_directory_link(path):
    if os.name == "nt":
        path.rmdir()  # Remove the junction itself, never its target.
    else:
        path.unlink()


def test_runner_rejects_nested_directory_link_and_releases_pins(synthetic, tmp_path):
    directory, _ = synthetic
    source = directory / "source"
    target = tmp_path / "source target"
    source.rename(target)
    directory_link(source, target)
    try:
        with pytest.raises(BundleError, match="linked|reparse"):
            RunnerBundle(directory)
    finally:
        remove_directory_link(source)
        target.rename(source)
    # Failed verification must not leave Windows directory handles pinned.
    relocated = tmp_path / "after rejected link"
    directory.rename(relocated)
    assert RunnerBundle(relocated).component() == (relocated / "runner.wasm").read_bytes()


def test_later_reads_reject_replaced_bundle_path_even_with_identical_bytes(synthetic, tmp_path):
    directory, _ = synthetic
    bundle = RunnerBundle(directory)
    target = tmp_path / "original bundle"
    directory.rename(target)  # Successful verification must release its pins too.
    directory_link(directory, target)
    try:
        with pytest.raises(BundleError, match="linked|reparse"):
            bundle.verify()
        with pytest.raises(BundleError, match="linked|reparse"):
            bundle.component()
    finally:
        remove_directory_link(directory)
        target.rename(directory)


def test_runner_rejects_file_replacement_between_inventory_and_read(synthetic, tmp_path, monkeypatch):
    directory, _ = synthetic
    original = artifacts.disk.read_file
    replacement = tmp_path / "same bytes, different file"
    replacement.write_bytes((directory / "runner.wasm").read_bytes())

    def replace_before_read(path, parent, maximum, expected=None):
        if path.name == "runner.wasm":
            replacement.replace(path)
        return original(path, parent, maximum, expected)

    monkeypatch.setattr(artifacts.disk, "read_file", replace_before_read)
    with pytest.raises(BundleError) as caught:
        RunnerBundle(directory)
    if os.name == "nt":
        # Windows pins deny replacement; POSIX detects the changed file identity
        # when it opens the entry relative to its pinned parent.
        assert getattr(caught.value.__cause__, "winerror", None) == 32
    else:
        assert "changed before reading" in str(caught.value)


def test_full_verification_checks_source_hashes_again(synthetic):
    directory, _ = synthetic
    bundle = RunnerBundle(directory)
    source = directory / artifacts.SOURCES[0]
    content = source.read_bytes()
    source.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises(BundleError, match="differs from receipt"):
        bundle.verify()


def test_real_bundle_retains_upstream_texts(runner_bundle):
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
