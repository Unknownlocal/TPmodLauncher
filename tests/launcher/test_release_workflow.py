# SPDX-License-Identifier: MPL-2.0
"""Exercise a source-only release with real wheel installation and archive checks.

Native compilation, source fetching, runner compilation and executable launch
are external boundaries supplied by owned fixtures. Nothing comes from an old
launcher installation or a developer wheel cache.
"""

import base64
import csv
import hashlib
from importlib.metadata import version
import io
import json
import zipfile

import pytest

from launcher import release_workflow as workflow
from mod_runtime import bundle

pytestmark = pytest.mark.release


def wheel_bytes(name, package_version):
    info = f"{name}-{package_version}.dist-info"
    files = {
        f"{name}/__init__.py": b"# Owned wheel fixture\n",
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {package_version}\nRequires-Python: >=3.13\n".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{info}/licenses/LICENSE": b"Owned fixture, no third-party code\n",
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        writer.writerow([path, "sha256=" + digest, len(data)])
    writer.writerow([f"{info}/RECORD", "", ""])
    files[f"{info}/RECORD"] = record.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return output.getvalue()


@pytest.fixture
def clean_source(tmp_path, monkeypatch):
    root = tmp_path / "source checkout with spaces"
    root.mkdir()
    events = []
    for directory in (*workflow.packaging.SOURCE_DIRECTORIES, "notices"):
        (root / directory).mkdir()
        (root / directory / "owned.py").write_text("# Source-only fixture\n")
    (root / "launcher/frida_build.toml").write_text("# Fixture recipe\n")
    for name in ("README.md", "LICENCE", "ATTRIBUTION.txt"):
        (root / name).write_text("Owned source fixture\n")
    runtime = io.BytesIO()
    with zipfile.ZipFile(runtime, "w") as archive:
        archive.writestr("python.exe", b"Inert owned runtime fixture")
    runtime_data = runtime.getvalue()
    (root / "pyproject.toml").write_text(
        '[tool.modlauncher.runtime]\nversion = "3.13.16"\n'
        'directory = "libs/python-fixture"\nfilename = "runtime.zip"\n'
        f'sha256 = "{hashlib.sha256(runtime_data).hexdigest()}"\n'
        f'[tool.modlauncher.build]\npip_version = "{version("pip")}"\n'
    )
    companion = wheel_bytes("demo", "1.0")
    (root / "pylock.toml").write_text(
        'lock-version = "1.0"\ncreated-by = "owned fixture"\nrequires-python = "==3.13.*"\n'
        'environments = ["sys_platform == \'win32\' and platform_machine == \'AMD64\'"]\n'
        'extras = []\ndependency-groups = []\n'
        '[[packages]]\nname = "frida"\nversion = "1.0+local"\nrequires-python = ">=3.13"\n'
        '[[packages.wheels]]\nname = "frida-1.0+local-py3-none-any.whl"\n'
        'path = ".cache/wheels/frida-1.0+local-py3-none-any.whl"\n'
        f'hashes = {{sha256 = "{"0" * 64}"}}\n'
        '[[packages]]\nname = "demo"\nversion = "1.0"\nrequires-python = ">=3.13"\n'
        '[[packages.wheels]]\nname = "demo-1.0-py3-none-any.whl"\n'
        'path = ".cache/wheels/demo-1.0-py3-none-any.whl"\n'
        'url = "https://owned.invalid/demo.whl"\n'
        f'hashes = {{sha256 = "{hashlib.sha256(companion).hexdigest()}"}}\n'
    )
    recipe = {"frida": {"package_version": "1.0+local"}}
    monkeypatch.setattr(workflow.build, "read_recipe", lambda *args: recipe)
    monkeypatch.setattr(workflow.build, "host_target", lambda recipe: "windows-x86_64")

    def compile_frida(recipe, target, work, output, cache):
        events.append("compile-frida")
        assert target == "windows-x86_64" and not output.exists()
        (work / "source").mkdir()
        (work / "source/owned-marker").write_text("this build's source workspace")
        output.mkdir()
        wheel = output / "frida-1.0+local-py3-none-any.whl"
        wheel.write_bytes(wheel_bytes("frida", "1.0+local"))
        (output / "build-details.json").write_text(json.dumps({"wheel": workflow.build.file_record(wheel)}))
        return wheel

    def verify_build(output, recipe, target):
        data = json.loads((output / "build-details.json").read_text())
        wheel = output / data["wheel"]["name"]
        assert workflow.build.file_record(wheel) == data["wheel"]
        return wheel

    def export_sources(work, build_output, output):
        events.append("export-matching-source")
        assert (work / "source/owned-marker").is_file()
        output.mkdir()
        archive = output / "frida-sources.tar.xz"
        archive.write_bytes(b"Owned source-archive fixture")
        (output / "source-details.json").write_text(json.dumps({
            "build_details_sha256": workflow.build.file_record(build_output / "build-details.json")["sha256"],
            "archive": workflow.build.file_record(archive),
        }))

    def authenticate_runtime(root, settings):
        events.append("authenticate-runtime")
        target = root / ".cache/downloads/runtime.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(runtime_data)
        return target

    def compile_runner(root, output):
        events.append("compile-runner")
        output.mkdir(parents=True)
        records = {}
        for name in bundle.FILES:
            data = b"\0asm\x0d\0\x01\0" if name == "runner.wasm" else b"Owned runner fixture\n"
            target = output / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
            records[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        (output / bundle.MANIFEST).write_text(json.dumps({
            "format": 1, "abi": bundle.ABI_VERSION,
            "componentize_py": bundle.COMPONENTIZE_VERSION, "files": records,
        }))

    def check_release(candidate):
        events.append("verify-executable")
        assert (candidate / "libs/python-fixture/python.exe").is_file()
        assert workflow.paths.verify_bundle(candidate).is_dir()
        bundle.RunnerBundle(candidate / bundle.DIRECTORY)

    def download(url, timeout):
        assert url == "https://owned.invalid/demo.whl"
        events.append("download-companion")
        return io.BytesIO(companion)

    monkeypatch.setattr(workflow.build, "build", compile_frida)
    monkeypatch.setattr(workflow.build, "verify_output", verify_build)
    monkeypatch.setattr(workflow.sources, "create", export_sources)
    monkeypatch.setattr(workflow.release, "authenticate_runtime", authenticate_runtime)
    monkeypatch.setattr(workflow.release.urllib.request, "urlopen", download)
    monkeypatch.setattr(workflow, "build_runner", compile_runner)
    monkeypatch.setattr(workflow.packaging, "check_release", check_release)
    # The native compiler's short workspace is owned by this test as well.
    workspace = tmp_path / "native-workspace"
    workspace.mkdir()
    monkeypatch.setattr(workflow, "short_workspace", lambda: workspace)
    return root, events


def test_clean_checkout_builds_a_paired_release_without_rewriting_source_pins(clean_source):
    root, events = clean_source
    original = (root / "pylock.toml").read_bytes()
    assert not (root / ".cache").exists() and not (root / "libs").exists()
    archive = workflow.run(root, "1.0")
    assert archive.parent == root / "dist"
    assert archive.with_suffix(".zip.sha256").is_file()
    assert events[:2] == ["compile-frida", "export-matching-source"]
    assert "download-companion" in events and "compile-runner" in events
    assert events.count("verify-executable") == 2
    assert (root / "pylock.toml").read_bytes() == original
    assert not (root / "libs").exists() and not (root / "bundled_packages.toml").exists()
    with zipfile.ZipFile(archive) as package:
        generated_lock = package.read("modLauncher/source/release/pylock.toml")
        assert b'"' + b"0" * 64 + b'"' not in generated_lock
        assert "modLauncher/source/frida/frida-sources.tar.xz" in package.namelist()
        assert "modLauncher/SteamLaunchOptions.cmd" in package.namelist()
        assert not any("/.cache/" in name for name in package.namelist())


def test_failed_extracted_launch_check_never_publishes_zip(clean_source, monkeypatch):
    root, _ = clean_source
    original_check = workflow.packaging.check_release
    def check(candidate):
        if candidate.name == "modLauncher":
            raise RuntimeError("owned extracted-runtime failure")
        original_check(candidate)
    monkeypatch.setattr(workflow.packaging, "check_release", check)
    with pytest.raises(RuntimeError, match="owned extracted-runtime failure"):
        workflow.run(root, "bad-extraction")
    assert list((root / "dist").iterdir()) == []


def test_platforms_keep_separate_outputs_candidates_and_check_only_records(clean_source, monkeypatch):
    root, events = clean_source
    windows = workflow.run(root, "0.1.0")
    windows_data = windows.read_bytes()
    windows_record = root / ".cache/releases/windows-0.1.0.json"
    windows_receipt = windows_record.read_bytes()
    record = json.loads(windows_receipt)
    # Reuse the paired native inputs, as both player platforms use Windows Python.
    monkeypatch.setattr(workflow, "ROOT", root)
    assert workflow.main(["--version", "0.1.0", "--platform", "linux",
                          "--frida-build", record["build"], "--frida-sources", record["sources"]]) == 0
    linux = root / "dist/modLauncher-0.1.0-linux-proton-x64.zip"
    linux_record = root / ".cache/releases/linux-0.1.0.json"
    assert linux.is_file() and linux.with_suffix(".zip.sha256").is_file()
    assert windows.read_bytes() == windows_data and windows_record.read_bytes() == windows_receipt
    assert events.count("compile-frida") == 1
    linux_receipt = json.loads(linux_record.read_bytes())
    assert linux_receipt["candidate"] != record["candidate"]
    before_checks = events.copy()
    assert workflow.run(root, "0.1.0", check_only=True) == root / record["candidate"]
    assert workflow.run(root, "0.1.0", platform="linux", check_only=True) == root / linux_receipt["candidate"]
    assert events[len(before_checks):] == ["verify-executable", "verify-executable"]
    for platform in ("windows", "linux"):
        with pytest.raises(workflow.paths.DependencyError, match="already exists"):
            workflow.run(root, "0.1.0", platform=platform)
    assert events[len(before_checks):] == ["verify-executable", "verify-executable"]
