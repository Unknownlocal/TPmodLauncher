# SPDX-License-Identifier: MPL-2.0
"""Validate manifests and the real Python author contract without game or pack discovery."""

from pathlib import Path
import os
import shutil
import subprocess

import pytest
from wasmtime import wat2wasm

from launcher import build_mod
from launcher.mod_contract import Manifest, ManifestError, Mod
from launcher.sandbox import SandboxError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
EXAMPLE = ROOT / "examples/diagnostic_mod"


@pytest.fixture
def author(tmp_path):
    folder = tmp_path / "owned mod"
    folder.mkdir()
    shutil.copyfile(EXAMPLE / "mod.toml", folder / "mod.toml")
    return folder


def tiny_component(folder, result="{}"):
    source = (
        (FIXTURES / "contract.wat")
        .read_text()
        .replace("@RESULT@", result)
        .replace("@LENGTH@", str(len(result)))
    )
    (folder / "mod.wasm").write_bytes(wat2wasm(source))
    return Manifest.read(folder)


@pytest.mark.parametrize(
    "old,new",
    [
        ("schema = 1", "schema = true"),
        ('api = "0.1.0"', 'api = "9.0.0"'),
        ('runtime = "python"', 'runtime = "native"'),
        ('version = "0.1.0"', 'version = "01.1.0"'),
        ('id = "example.diagnostics"', 'id = "../other"'),
        ('component = "mod.wasm"', 'component = "../outside.wasm"'),
        ('component = "mod.wasm"', 'component = "C:/outside.wasm"'),
        ('component = "mod.wasm"', 'component = "nested//mod.wasm"'),
        ('component = "mod.wasm"', 'component = "mod.py"'),
        ('["diagnostics"]', '["frida"]'),
        ('["diagnostics"]', '["diagnostics", "diagnostics"]'),
        ("schema = 1", 'schema = 1\nentry = "arbitrary.py"'),
    ],
)
def test_manifest_rejects_unsupported_or_escaping_metadata(author, old, new):
    path = author / "mod.toml"
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(ManifestError):
        Manifest.read(author)


def test_manifest_is_data_only_and_component_can_be_built_later(author):
    path = author / "mod.toml"
    path.write_text(path.read_text().replace('"0.1.0"', '"0.1.0-beta.1+build.2"', 1))
    metadata = Manifest.read(author)
    assert metadata.version == "0.1.0-beta.1+build.2"
    assert metadata.capabilities == frozenset({"diagnostics"})
    with pytest.raises(ManifestError):
        metadata.read_component()


def test_manifest_read_is_size_limited(author):
    (author / "mod.toml").write_bytes(b"#" * 16_385)
    with pytest.raises(ManifestError, match="16 KiB"):
        Manifest.read(author)


def test_requested_diagnostics_requires_a_trusted_provider(author):
    cleaned = []
    with pytest.raises(ManifestError, match="trusted report"):
        Mod(Manifest.read(author), cleanup=lambda: cleaned.append(True))
    assert cleaned == [True]


def test_cleanup_runs_when_component_read_fails_before_sandbox_creation(author):
    cleaned = []
    with pytest.raises(ManifestError):
        Mod(
            Manifest.read(author),
            report=lambda *_: None,
            cleanup=lambda: cleaned.append(True),
        )
    assert cleaned == [True]


def test_replaced_mod_folder_cannot_redirect_component_reads(author):
    manifest = tiny_component(author)
    saved = author.with_name("saved owned mod")
    author.rename(saved)
    if os.name == "nt":
        # A junction is available to ordinary Windows users and both folders are pytest-owned.
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(author), str(saved)],
            check=True,
            capture_output=True,
        )
    else:
        author.symlink_to(saved, target_is_directory=True)
    try:
        with pytest.raises(ManifestError, match="folder changed"):
            manifest.read_component()
    finally:
        # Remove only the link, preserving the actual directory and its component.
        author.rmdir() if os.name == "nt" else author.unlink()
    assert (saved / "mod.wasm").is_file()


def test_component_imports_cannot_expand_manifest_permissions(author):
    cleaned = []
    tiny_component(author)
    path = author / "mod.toml"
    path.write_text(path.read_text().replace('["diagnostics"]', "[]"))
    with pytest.raises(SandboxError, match="approved host operation"):
        Mod(
            Manifest.read(author),
            report=lambda *_: pytest.fail("No diagnostic grant"),
            cleanup=lambda: cleaned.append(True),
        )
    assert cleaned == [True]


@pytest.mark.parametrize("result", ["{", "NaN", "9223372036854775808"])
def test_invalid_guest_json_disables_and_cleans_the_mod(author, result):
    cleaned = []
    with Mod(
        tiny_component(author, result),
        report=lambda *_: None,
        cleanup=lambda: cleaned.append(True),
    ) as mod:
        mod.activate()
        with pytest.raises(SandboxError):
            mod.event("owned.event", {})
        assert mod.sandbox.closed and cleaned == [True]


def test_lifecycle_and_required_export_shape_are_enforced(author):
    manifest = tiny_component(author)
    with Mod(manifest, report=lambda *_: None) as mod:
        with pytest.raises(SandboxError, match="Activate"):
            mod.event("owned.event", {})
        mod.activate()
        with pytest.raises(SandboxError, match="already"):
            mod.activate()
        assert mod.event("owned.event", {}) == {}
    source = (
        (FIXTURES / "contract.wat")
        .read_text()
        .replace("@RESULT@", "{}")
        .replace("@LENGTH@", "2")
        .replace('(export "activate") (canon lift', '(export "other") (canon lift')
    )
    (author / "mod.wasm").write_bytes(wat2wasm(source))
    with pytest.raises(SandboxError, match="required API"):
        Mod(manifest, report=lambda *_: None)


def test_python_build_and_real_guest_contract(built_python_mod, tmp_path, monkeypatch):
    folder = built_python_mod
    assert not (folder / "src/build-marker.txt").exists()
    assert not (folder / "src/runtime-write.txt").exists()
    assert not (folder / "src/__pycache__").exists()
    # A second manifest labels a separate instance of the same portable bytes.
    other = tmp_path / "second"
    other.mkdir()
    (other / "mod.toml").write_text(
        (folder / "mod.toml")
        .read_text()
        .replace("example.diagnostics", "example.second")
    )
    shutil.copyfile(folder / "mod.wasm", other / "mod.wasm")
    monkeypatch.setenv("MODLAUNCHER_TEST_SECRET", "host-secret-not-for-guests")
    messages = []
    with Mod(
        Manifest.read(folder), report=lambda *message: messages.append(message)
    ) as first, Mod(
        Manifest.read(other), report=lambda *message: messages.append(message)
    ) as second:
        first.activate()
        second.activate()
        assert messages == [
            ("example.diagnostics", "ready  "),
            ("example.second", "ready  "),
        ]
        assert first.event("echo", {"number": 3}) == {
            "count": 1,
            "value": {"number": 3},
        }
        assert first.event("echo", None)["count"] == 2
        assert second.event("echo", True) == {"count": 1, "value": True}
        probe = first.event("probe", None)
        assert probe == {
            "platform": "wasi",
            "secret": "",
            "ambient-package": False,
        }
        with pytest.raises(SandboxError) as denied:
            first.event("write", None)
        assert not any(
            word in str(denied.value) for word in ("fuel", "deadline", "interrupt")
        )
        assert first.sandbox.closed
        assert second.event("echo", None)["count"] == 2
        with pytest.raises(SandboxError) as denied:
            second.event("import-host", None)
        assert not any(
            word in str(denied.value) for word in ("fuel", "deadline", "interrupt")
        )
    assert not (folder / "src/runtime-write.txt").exists()


@pytest.mark.parametrize("operation", ["socket", "subprocess", "ctypes"])
def test_python_guest_cannot_obtain_native_host_io(built_python_mod, operation):
    with Mod(Manifest.read(built_python_mod), report=lambda *_: None) as mod:
        mod.activate()
        try:
            result = mod.event(operation, None)
        except SandboxError as denied:
            # A WASI trap or an unavailable Python operation both deny the request.
            # Budget exhaustion is not evidence that the requested operation was denied.
            assert not any(
                word in str(denied) for word in ("fuel", "deadline", "interrupt")
            )
            assert mod.sandbox.closed
        else:
            assert result is False


def test_builder_preserves_existing_output_and_rejects_non_source(author):
    (author / "mod.wasm").write_bytes(b"previous release")
    with pytest.raises(FileExistsError):
        build_mod.build(author, author / "mod.wasm")
    assert (author / "mod.wasm").read_bytes() == b"previous release"
    (author / "src").mkdir()
    (author / "src/native.pyd").write_bytes(b"not accepted")
    with pytest.raises(ValueError, match="Python source"):
        build_mod.build(author, author / "new.wasm")
