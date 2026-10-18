# SPDX-License-Identifier: MPL-2.0
"""Check source pins, wheel identity, cached outputs and real reduced-binding behaviour."""

import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from launcher import build_frida as build

pytestmark = pytest.mark.release

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def recipe():
    return build.read_recipe()


@pytest.mark.parametrize("target", ["windows-x86_64", "linux-x86_64"])
def test_reduced_build_does_not_invoke_node(recipe, target, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The reduced Windows/Linux build must work without Node installed")
    monkeypatch.setattr(build.subprocess, "run", forbidden)
    assert build.node_version(recipe, target) is None


@pytest.fixture
def wheel_output(tmp_path, recipe):
    """Make a small standard wheel for static verification; no fake binary is executed."""
    output = tmp_path / "output"
    output.mkdir()
    version = recipe["frida"]["package_version"]
    wheel = output / f"frida-{version}-cp37-abi3-win_amd64.whl"
    metadata = f"frida-{version}.dist-info"
    files = {
        "frida/__init__.py": b"# synthetic fixture\n",
        "frida/aio.py": b"# synthetic fixture\n",
        "frida/_frida.pyi": b"# synthetic fixture\n",
        "frida/py.typed": b"",
        "frida/_frida.pyd": b"synthetic, never executed",
        metadata + "/METADATA": f"Name: frida\nVersion: {version}\n".encode(),
        metadata
        + "/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: cp37-abi3-win_amd64\n",
        metadata + "/licenses/COPYING": b"synthetic fixture licence",
    }
    # Standard wheel records allow real pip installation; the synthetic binary is never loaded.
    files[metadata + "/RECORD"] = "".join(
        f"{name},,\n" for name in [*files, metadata + "/RECORD"]
    ).encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    details = {
        "recipe_sha256": build.recipe_digest(recipe),
        "target": "windows-x86_64",
        # Static fixture records have the right shape; real Git provenance is tested separately.
        "sources": {
            ".": recipe["frida"]["revision"],
            **{name: "1" * 40 for name in recipe["submodules"]},
        },
        "inputs": build.pinned_archives(recipe, "windows-x86_64"),
        "wheel": build.file_record(wheel),
        "extension": build.inspect_wheel(wheel, recipe, "windows-x86_64"),
    }
    (output / "build-details.json").write_text(json.dumps(details), encoding="utf-8")
    return output, wheel


@pytest.fixture
def source_checkout(tmp_path, source_repository):
    """Use a real local Git repository to exercise commit and dirty-checkout guards."""
    source = tmp_path / "source"
    recipe = {
        "frida": {"revision": source_repository(source)},
        "submodules": [],
    }
    return source, recipe


@pytest.fixture
def source_output(wheel_output, tmp_path):
    """Make a receipt-bound source placeholder; source restoration is tested separately."""
    binary, _ = wheel_output
    output = tmp_path / "source-output"
    output.mkdir()
    archive = output / "frida-sources.tar.xz"
    archive.write_bytes(b"synthetic source fixture, never executed")
    (output / "source-details.json").write_text(
        json.dumps(
            {
                "archive": build.file_record(archive),
                "build_details_sha256": build.file_record(
                    binary / "build-details.json"
                )["sha256"],
            }
        )
    )
    return output


def test_source_revision_mismatch_is_rejected(source_checkout):
    source, recipe = source_checkout
    assert build.verify_sources(source, recipe) == {".": recipe["frida"]["revision"]}
    recipe["frida"]["revision"] = "0" * 40
    with pytest.raises(build.Error, match="commit differs"):
        build.verify_sources(source, recipe)


def test_verified_output_is_reused_without_compilation(wheel_output, recipe, tmp_path):
    output, wheel = wheel_output
    # An occupied workspace and absent tool cache prove this path does not enter a build.
    work = tmp_path / "occupied"
    work.mkdir()
    (work / "marker").touch()
    assert (
        build.build(recipe, "windows-x86_64", work, output, tmp_path / "absent-cache")
        == wheel
    )
    assert (
        build.main(
            ["--verify-only", "--target", "windows-x86_64", "--output", str(output)]
        )
        == 0
    )


def test_reduced_candidate_uses_real_pip_and_preserves_active_files(
    wheel_output, source_output, project_factory, monkeypatch, tmp_path, runner_bundle
):
    root, _ = project_factory()
    from mod_runtime.bundle import DIRECTORY, RunnerBundle
    runner_bundle.copy_to(root / DIRECTORY)
    output, wheel = wheel_output
    for name in ("launcher", "hook_manager", "mod_runtime", "notices", "examples"):
        (root / name).mkdir()
        (root / name / "fixture.txt").write_text("synthetic source")
    for name in ("LICENCE", "ATTRIBUTION.txt", "README.md"):
        (root / name).write_text("synthetic fixture")
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as runtime:
        runtime.writestr("python.exe", b"synthetic runtime, never executed")
    # Only external identity services are replaced; wheel installation and file checks are real.
    original_run = subprocess.run
    verified = []

    def verify_or_run(command, **kwargs):
        if command[:3] == ["gh", "attestation", "verify"]:
            verified.append(Path(command[3]).name)
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, **kwargs)

    monkeypatch.setattr(build.release.subprocess, "run", verify_or_run)
    monkeypatch.setattr(
        build.release, "authenticate_runtime", lambda root, runtime: archive
    )
    before = (root / "pylock.toml").read_bytes()
    candidate = build.release.prepare_reduced_release(
        root,
        output,
        ".cache/releases/reduced",
        "example/launcher",
        "1" * 40,
        source_output,
    )
    runtime = build.release.read_settings(candidate)["runtime"]
    bundled_runner = RunnerBundle(candidate / DIRECTORY)
    assert bundled_runner.component() == runner_bundle.component()
    for name in ("launcher", "hook_manager", "mod_runtime", "notices", "examples"):
        assert (candidate / name / "fixture.txt").read_text() == "synthetic source"
    packages = build.release.read_lock(candidate, runtime)
    assert [(package.name, str(package.version)) for package, _ in packages] == [
        ("frida", "17.23.3+modlauncher.1"),
        ("demo", "1.0"),
    ]
    assert verified == [
        wheel.name,
        "build-details.json",
        "frida-sources.tar.xz",
        "source-details.json",
    ]
    assert (candidate / "source/frida-sources.tar.xz").read_bytes() == (
        source_output / "frida-sources.tar.xz"
    ).read_bytes()
    installed = build.release.dependencies.verify_bundle(candidate)
    assert (installed / "frida/_frida.pyd").read_bytes() == b"synthetic, never executed"
    assert (installed / "demo/__init__.py").read_text() == "VALUE = 42\n"
    assert build.release.prepare_packages(candidate) == installed
    assert (root / "pylock.toml").read_bytes() == before
    assert not (root / "bundled_packages.toml").exists()


def test_changed_wheel_during_authentication_cannot_be_published(
    wheel_output, source_output, project_factory, monkeypatch
):
    root, _ = project_factory()
    output, wheel = wheel_output

    def alter(command, **kwargs):
        if Path(command[3]).name == "build-details.json":
            with wheel.open("ab") as stream:
                stream.write(b"changed during identity check")

    monkeypatch.setattr(build.release.subprocess, "run", alter)
    with pytest.raises(
        build.release.dependencies.DependencyError,
        match="changed during authentication",
    ):
        build.release.prepare_reduced_release(
            root,
            output,
            ".cache/releases/reduced",
            "example/launcher",
            "1" * 40,
            source_output,
        )
    assert not (root / ".cache/releases").exists()


@pytest.mark.frida_build
def test_real_reduced_binding_has_required_features_and_child_gating(tmp_path, recipe):
    output = os.environ.get("TPM_FRIDA_BUILD_OUTPUT")
    if not output:
        if os.environ.get("TPM_REQUIRE_FRIDA_BUILD_TESTS") == "1":
            pytest.fail(
                "Set TPM_FRIDA_BUILD_OUTPUT to the built wheel/details directory"
            )
        pytest.skip("Build a reduced Frida wheel to run native acceptance")
    target = build.host_target(recipe)
    wheel = build.verify_output(Path(output), recipe, target)
    package = tmp_path / "package"
    package.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        # inspect_wheel has already checked every member's path and type.
        archive.extractall(package)
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(FIXTURES / "reduced_frida_probe.py"),
            str(package),
            recipe["frida"]["version"],
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "quickjs": True,
        "native_hook": True,
        "sqlite_absent": True,
        "v8_disabled": True,
        "child_gating": True,
        "script_rpc": True,
        "stdio": True,
    }
