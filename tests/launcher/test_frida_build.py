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

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def recipe():
    return build.read_recipe()


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


def test_recipe_hash_survives_git_line_ending_changes(tmp_path):
    path = tmp_path / "recipe.toml"
    text = build.RECIPE.read_text()
    path.write_bytes(text.replace("\r\n", "\n").encode())
    original = build.recipe_digest(build.read_recipe(path))
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode())
    assert build.recipe_digest(build.read_recipe(path)) == original


def test_source_revision_mismatch_is_rejected(source_checkout):
    source, recipe = source_checkout
    assert build.verify_sources(source, recipe) == {".": recipe["frida"]["revision"]}
    recipe["frida"]["revision"] = "0" * 40
    with pytest.raises(build.Error, match="commit differs"):
        build.verify_sources(source, recipe)


def test_submodule_commits_are_derived_from_parent_git_links(
    source_checkout, source_repository
):
    source, recipe = source_checkout
    child = source / "child"
    revision = source_repository(child)
    build.git(
        source, "update-index", "--add", "--cacheinfo", f"160000,{revision},child"
    )
    build.git(source, "commit", "-m", "Pin synthetic child")
    recipe["frida"]["revision"] = build.git(source, "rev-parse", "HEAD")
    recipe["submodules"] = ["child"]
    assert build.verify_sources(source, recipe)["child"] == revision
    # Move the child to a different real commit without changing the parent's recorded commit.
    (child / "code.py").write_text("VALUE = 2\n")
    build.git(child, "add", "code.py")
    build.git(child, "commit", "-m", "Different synthetic child")
    with pytest.raises(build.Error, match="commit differs"):
        build.verify_sources(source, recipe)


def test_nested_submodule_uses_its_verified_parent_commit(
    source_checkout, source_repository
):
    source, recipe = source_checkout
    child = source / "child"
    nested = child / "nested"
    # Commit the leaf first, then record its Git link in the child repository.
    nested_revision = source_repository(nested)
    child_revision = source_repository(child, [("nested", nested_revision)])
    build.git(
        source,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{child_revision},child",
    )
    build.git(source, "commit", "-m", "Pin synthetic child")
    recipe["frida"]["revision"] = build.git(source, "rev-parse", "HEAD")
    # Deliberately list the leaf first; verification must still check its parent first.
    recipe["submodules"] = ["child/nested", "child"]
    assert build.verify_sources(source, recipe) == {
        ".": recipe["frida"]["revision"],
        "child": child_revision,
        "child/nested": nested_revision,
    }
    (nested / "code.py").write_text("VALUE = 2\n")
    with pytest.raises(build.Error, match="contains changes: child/nested"):
        build.verify_sources(source, recipe)


def test_ordinary_file_cannot_be_used_as_a_required_submodule(source_checkout):
    source, recipe = source_checkout
    recipe["submodules"] = ["code.py"]
    with pytest.raises(build.Error, match="not a submodule"):
        build.verify_sources(source, recipe)


def test_clone_selects_pinned_tag_instead_of_later_development_commit(
    source_checkout, tmp_path
):
    source, recipe = source_checkout
    pinned = recipe["frida"]["revision"]
    build.git(source, "tag", "17.23.3")
    (source / "code.py").write_text("VALUE = 2\n")
    build.git(source, "add", "code.py")
    build.git(source, "commit", "-m", "Later development")
    assert build.git(source, "rev-parse", "HEAD") != pinned
    # Exercise the actual clone command against a local fixture, without network or Git mocks.
    recipe["frida"].update(repository=source.as_uri(), version="17.23.3")
    tagged = tmp_path / "tagged clone"
    assert build.checkout(tagged, recipe) == {".": pinned}
    assert (tagged / "code.py").read_text() == "VALUE = 1\n"


@pytest.mark.parametrize("filename", ["code.py", "unexpected.py"])
def test_changed_or_untracked_source_is_rejected(source_checkout, filename):
    source, recipe = source_checkout
    (source / filename).write_text("VALUE = 2\n")
    with pytest.raises(build.Error, match="contains changes"):
        build.verify_sources(source, recipe)


def test_verified_archive_cache_is_used_without_a_download(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / "sdk-fixture.tar.xz"
    archive.write_bytes(b"synthetic cached archive")
    record = build.file_record(archive)
    # A successful run cannot fetch this nonexistent URL.
    record["url"] = (tmp_path / "absent-download").as_uri()
    destination = tmp_path / "deps"
    build.stage_archives(cache, destination, [record])
    assert (destination / archive.name).read_bytes() == archive.read_bytes()


def test_bad_archive_hash_does_not_reach_upstream_build_cache(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / "sdk-fixture.tar.xz"
    archive.write_bytes(b"old cached bytes")
    source = tmp_path / "bad-download"
    source.write_bytes(b"bad downloaded bytes")
    record = {"name": archive.name, "url": source.as_uri(), "sha256": "0" * 64}
    destination = tmp_path / "deps"
    with pytest.raises(build.Error, match="SHA-256 mismatch"):
        build.stage_archives(cache, destination, [record])
    assert not (destination / archive.name).exists()
    assert archive.read_bytes() == b"old cached bytes"


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


def test_wheel_corruption_is_rejected_before_reuse(wheel_output, recipe):
    output, wheel = wheel_output
    with wheel.open("ab") as stream:
        stream.write(b"unexpected change")
    with pytest.raises(build.Error, match="SHA-256 or size"):
        build.verify_output(output, recipe, "windows-x86_64")


def test_changed_features_invalidate_cached_output(wheel_output, recipe):
    output, _ = wheel_output
    recipe["frida"]["options"] = ["-Dfrida-gum:v8=enabled"]
    with pytest.raises(build.Error, match="do not match"):
        build.verify_output(output, recipe, "windows-x86_64")


@pytest.mark.parametrize("change", ["root", "missing_submodule"])
def test_cached_source_record_must_match_root_pin_and_required_paths(
    wheel_output, recipe, change
):
    output, _ = wheel_output
    path = output / "build-details.json"
    details = json.loads(path.read_text())
    if change == "root":
        details["sources"]["."] = "0" * 40
    else:
        del details["sources"][recipe["submodules"][0]]
    path.write_text(json.dumps(details), encoding="utf-8")
    with pytest.raises(build.Error, match="do not match"):
        build.verify_output(output, recipe, "windows-x86_64")


def test_output_rejects_extra_files(wheel_output, recipe):
    output, _ = wheel_output
    (output / "unexpected.whl").write_bytes(b"fixture")
    with pytest.raises(build.Error, match="unexpected files"):
        build.verify_output(output, recipe, "windows-x86_64")


@pytest.mark.parametrize("target", ["linux-x86_64", "macos-arm64"])
def test_wrong_platform_wheel_is_rejected(wheel_output, recipe, target):
    _, wheel = wheel_output
    with pytest.raises(build.Error, match="platform differs"):
        build.inspect_wheel(wheel, recipe, target)


def test_official_version_cannot_be_mistaken_for_our_build(wheel_output, recipe):
    _, wheel = wheel_output
    stock = wheel.with_name(wheel.name.replace("+modlauncher.1", ""))
    wheel.rename(stock)
    with pytest.raises(build.Error, match="version"):
        build.inspect_wheel(stock, recipe, "windows-x86_64")


@pytest.mark.parametrize("change", ["metadata", "licence"])
def test_incomplete_or_mislabelled_wheel_is_rejected(wheel_output, recipe, change):
    _, wheel = wheel_output
    with zipfile.ZipFile(wheel) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if change == "metadata":
        name = next(name for name in files if name.endswith("/METADATA"))
        files[name] = b"Name: frida\nVersion: 1.0\n"
    else:
        name = next(name for name in files if name.endswith("/licenses/COPYING"))
        del files[name]
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with pytest.raises(build.Error, match="metadata differs|missing"):
        build.inspect_wheel(wheel, recipe, "windows-x86_64")


def test_occupied_workspace_is_never_overwritten(tmp_path, recipe):
    work = tmp_path / "work"
    work.mkdir()
    marker = work / "marker"
    marker.write_text("keep")
    with pytest.raises(build.Error, match="empty ordinary"):
        build.build(
            recipe, "windows-x86_64", work, tmp_path / "output", tmp_path / "cache"
        )
    assert marker.read_text() == "keep"


@pytest.mark.parametrize(
    "repository,commit,output",
    [
        ("", "1" * 40, ".cache/releases/reduced"),
        ("example/launcher", "release-tag", ".cache/releases/reduced"),
        ("example/launcher", "1" * 40, "libs/vendor/reduced"),
    ],
)
def test_reduced_preparation_requires_expected_identity_and_isolated_destination(
    project_factory, repository, commit, output
):
    root, _ = project_factory()
    before = (root / "pylock.toml").read_bytes()
    with pytest.raises(build.release.dependencies.DependencyError):
        build.release.prepare_reduced_release(
            root, root / "absent-build", output, repository, commit
        )
    assert (root / "pylock.toml").read_bytes() == before
    assert not (root / "libs").exists()


def test_reduced_preparation_requires_corresponding_source(
    wheel_output, project_factory
):
    root, _ = project_factory()
    output, _ = wheel_output
    with pytest.raises(
        build.release.dependencies.DependencyError, match="requires --frida-sources"
    ):
        build.release.prepare_reduced_release(
            root, output, ".cache/releases/reduced", "example/launcher", "1" * 40
        )
    assert not (root / ".cache/releases").exists()


@pytest.mark.parametrize(
    "failed_artifact", ["wheel", "details", "source", "source_details"]
)
def test_reduced_preparation_stops_before_writes_on_signature_failure(
    wheel_output, source_output, project_factory, monkeypatch, failed_artifact
):
    root, _ = project_factory()
    output, wheel = wheel_output
    calls = []
    artifacts = {
        "wheel": wheel,
        "details": output / "build-details.json",
        "source": source_output / "frida-sources.tar.xz",
        "source_details": source_output / "source-details.json",
    }
    failed = artifacts[failed_artifact]

    def reject(command, **kwargs):
        calls.append(command)
        artifact = Path(command[3])
        if artifact == failed:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(build.release.subprocess, "run", reject)
    before = (root / "pylock.toml").read_bytes()
    with pytest.raises(subprocess.CalledProcessError):
        build.release.prepare_reduced_release(
            root,
            output,
            ".cache/releases/reduced",
            "example/launcher",
            "1" * 40,
            source_output,
        )
    assert not (root / ".cache/releases").exists()
    assert (root / "pylock.toml").read_bytes() == before
    assert len(calls) == list(artifacts).index(failed_artifact) + 1
    for command in calls:
        assert command[4:] == [
            "--repo",
            "example/launcher",
            "--signer-workflow",
            "example/launcher/.github/workflows/reduced-frida.yml",
            "--source-digest",
            "1" * 40,
            "--signer-digest",
            "1" * 40,
            "--deny-self-hosted-runners",
        ]


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
