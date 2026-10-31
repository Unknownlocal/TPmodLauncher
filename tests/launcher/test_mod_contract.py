# SPDX-License-Identifier: MPL-2.0
"""Validate manifests and the real Python author contract without game or pack discovery."""

from pathlib import Path
import shutil

import pytest
from wasmtime import wat2wasm

from launcher import build_mod
from launcher.mod_contract import Manifest, Mod
from launcher.sandbox import SandboxError

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).with_name("fixtures")
EXAMPLE = ROOT / "examples/diagnostic_mod"


@pytest.fixture
def author(tmp_path, legacy_diagnostic_manifest):
    folder = tmp_path / "owned mod"
    folder.mkdir()
    (folder / "mod.toml").write_text(legacy_diagnostic_manifest, encoding="utf-8")
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


def test_component_imports_cannot_expand_manifest_permissions(author):
    cleaned = []
    tiny_component(author)
    path = author / "mod.toml"
    path.write_text(path.read_text().replace('["diagnostics"]', "[]"))
    with pytest.raises(SandboxError, match="approved host operation"):
        Mod(
            Manifest.read(author),
            submit_log=lambda *_: pytest.fail("No diagnostic grant"),
            cleanup=lambda: cleaned.append(True),
        )
    assert cleaned == [True]


def test_lifecycle_and_required_export_shape_are_enforced(author):
    manifest = tiny_component(author)
    with Mod(manifest, submit_log=lambda *_: None) as mod:
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
        Mod(manifest, submit_log=lambda *_: None)


@pytest.mark.legacy
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
        Manifest.read(folder), submit_log=lambda *message: messages.append(message)
    ) as first, Mod(
        Manifest.read(other), submit_log=lambda *message: messages.append(message)
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
@pytest.mark.legacy
def test_python_guest_cannot_obtain_native_host_io(built_python_mod, operation):
    with Mod(Manifest.read(built_python_mod), submit_log=lambda *_: None) as mod:
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


@pytest.mark.legacy
def test_builder_preserves_existing_output_and_rejects_non_source(author):
    (author / "mod.wasm").write_bytes(b"previous release")
    with pytest.raises(FileExistsError):
        build_mod.build(author, author / "mod.wasm")
    assert (author / "mod.wasm").read_bytes() == b"previous release"
    (author / "src").mkdir()
    (author / "src/native.pyd").write_bytes(b"not accepted")
    with pytest.raises(ValueError, match="Python source"):
        build_mod.build(author, author / "new.wasm")


@pytest.mark.legacy
def test_current_api_build_can_request_diagnostics_without_menu_access(author):
    # Build against the current contract with only one optional import enabled.
    path = author / "mod.toml"
    path.write_text(path.read_text().replace('api = "0.1.0"', 'api = "0.2.0"'))
    shutil.copytree(EXAMPLE / "src", author / "src")
    build_mod.build(author, author / "mod.wasm")
    messages = []
    # No menu provider is supplied: accidentally enabling its import must fail admission.
    with Mod(
        Manifest.read(author), submit_log=lambda *message: messages.append(message)
    ) as mod:
        mod.activate()
        assert messages == [("example.diagnostics", "Diagnostic example activated")]
        assert mod.event("echo", 42) == {"event": "echo", "received": 42}
