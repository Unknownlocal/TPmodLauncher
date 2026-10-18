# SPDX-License-Identifier: MPL-2.0
"""Build local Windows release inputs from source, then prepare an isolated bundle.

Package-Release.ps1 owns developer setup. This module coordinates the existing
native builder, matching-source exporter, dependency preparation and packager.
Local build hashes record integrity; they do not claim hosted provenance.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "_workflow_package", Path(__file__).with_name("package_release.py")
)
packaging = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(packaging)
build, release, sources = packaging.build, packaging.release, packaging.sources
paths = packaging.paths


def local_lock(runtime, selected, wheel):
    """Keep companion pins; replace only Frida's wheel hash in generated staging."""
    version = ".".join(runtime["version"].split(".")[:2])
    lines = ['lock-version = "1.0"', 'created-by = "modLauncher local release build"',
             f'requires-python = "=={version}.*"',
             'environments = ["sys_platform == \'win32\' and platform_machine == \'AMD64\'"]',
             "extras = []", "dependency-groups = []"]
    for package, pinned in selected:
        frida = package.name == "frida"
        record = build.file_record(wheel) if frida else None
        lines += ["", "[[packages]]", "name = " + json.dumps(package.name),
                  "version = " + json.dumps(str(package.version))]
        if package.requires_python:
            lines.append("requires-python = " + json.dumps(str(package.requires_python)))
        lines += ["[[packages.wheels]]",
                  "name = " + json.dumps(wheel.name if frida else pinned.filename),
                  "path = " + json.dumps(".cache/wheels/" + wheel.name if frida else pinned.path),
                  "hashes = {sha256 = " + json.dumps(record["sha256"] if frida else pinned.hashes["sha256"]) + "}"]
        if not frida:
            if pinned.url:
                lines.append("url = " + json.dumps(pinned.url))
            for identity in package.attestation_identities or []:
                lines += ["[[packages.attestation-identities]]",
                          "kind = " + json.dumps(identity["kind"]),
                          "repository = " + json.dumps(identity["repository"])]
    return ("\n".join(lines) + "\n").encode("utf-8")


def short_workspace():
    return Path(tempfile.mkdtemp(prefix="mlf-"))


def build_inputs(root, version):
    """Build and export together in a new generation; no retained wheel is needed."""
    recipe = build.read_recipe(root / "launcher/frida_build.toml")
    if build.host_target(recipe) != "windows-x86_64":
        raise paths.DependencyError("Build Windows releases on Windows, including Windows PowerShell invoked from WSL")
    generation = paths.local_path(root, f".cache/release-builds/{version}-{uuid.uuid4().hex[:8]}")
    generation.mkdir(parents=True)
    # Short Windows paths are required by the upstream native build tools. Keep the
    # workspace after export/failure for diagnostics; it is never distributed.
    work = short_workspace()
    output, source_output = generation / "frida", generation / "sources"
    (generation / "workspace.txt").write_text(str(work), encoding="utf-8")
    print(f"Frida build workspace: {work}", flush=True)
    build.build(recipe, "windows-x86_64", work, output,
                paths.local_path(root, ".cache/reduced-frida/archives"))
    sources.create(work, output, source_output)
    return output, source_output


def build_runner(root, destination):
    subprocess.run([sys.executable, "-B", "-m", "mod_runtime.build", "--output", str(destination)],
                   cwd=root, check=True)


def prepare_candidate(root, destination, build_output, source_output):
    """Prepare a fresh release without changing the source checkout's locks or libs."""
    settings = release.read_settings(root)["runtime"]
    selected = release.read_lock(root, settings)
    recipe = build.read_recipe(root / "launcher/frida_build.toml")
    wheel = build.verify_output(build_output, recipe, "windows-x86_64")
    selected_frida = [package for package, _ in selected if package.name == "frida"]
    if len(selected_frida) != 1 or str(selected_frida[0].version) != recipe["frida"]["package_version"]:
        raise paths.DependencyError("The Frida source recipe and selected package version disagree")
    sources.verify(source_output, build_output)
    companions = [(package, pinned) for package, pinned in selected if package.name != "frida"]
    release.prepare_wheels(root, companions)
    runtime_archive = release.authenticate_runtime(root, settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise paths.DependencyError("Use a fresh release candidate directory")
    with release.build_directory(destination.parent, ".release-stage-") as stage:
        for directory in (*packaging.SOURCE_DIRECTORIES, "notices"):
            origin = paths.local_path(root, directory)
            for _ in paths.tree_paths(origin):
                pass
            shutil.copytree(origin, stage / directory, copy_function=shutil.copyfile,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        for name in ("README.md", "LICENCE", "ATTRIBUTION.txt", "pyproject.toml"):
            shutil.copyfile(paths.local_path(root, name), stage / name)
        cached_wheel = paths.local_path(stage, ".cache/wheels/" + wheel.name)
        cached_wheel.parent.mkdir(parents=True)
        shutil.copyfile(wheel, cached_wheel)
        for _, pinned in companions:
            shutil.copyfile(paths.local_path(root, pinned.path), paths.local_path(stage, pinned.path))
        # The fresh local binary is selected only in this candidate. Never rewrite
        # the repository's reviewed dependency lock or active installation.
        (stage / "pylock.toml").write_bytes(local_lock(settings, selected, wheel))
        cached_runtime = paths.local_path(stage, ".cache/downloads/" + settings["filename"])
        cached_runtime.parent.mkdir(parents=True)
        shutil.copyfile(runtime_archive, cached_runtime)
        release.prepare_runtime(stage, settings, cached_runtime)
        release.prepare_packages(stage, refresh_manifest=True)
        build_runner(root, stage / "libs/mod-runners")
        # Detect inputs changing during preparation before retaining the candidate.
        build.verify_output(build_output, recipe, "windows-x86_64")
        sources.verify(source_output, build_output)
        if build.file_record(cached_wheel) != build.file_record(wheel):
            raise paths.DependencyError("Frida wheel changed during preparation")
        stage.rename(destination)
    return destination


def candidate_materials(candidate, build_output, source_output):
    manifest, materials = packaging.verify_inputs(candidate, build_output, source_output)
    materials.update({"source/release/pylock.toml": candidate / "pylock.toml",
                      "source/release/pyproject.toml": candidate / "pyproject.toml"})
    return manifest, materials


def run(root, version, *, check_only=False, prepare_only=False, build_output=None, source_output=None):
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}", version):
        raise paths.DependencyError("Invalid release version")
    if bool(build_output) != bool(source_output):
        raise paths.DependencyError("Supply both Frida overrides, or neither to build automatically")
    receipt = paths.local_path(root, f".cache/releases/windows-{version}.json")
    if check_only:
        if not receipt.is_file():
            raise paths.DependencyError("No prepared candidate exists for this version. Run without -CheckOnly first.")
        record = json.loads(receipt.read_text(encoding="utf-8"))
        relative = record["candidate"]
        if not relative.startswith(".cache/releases/"):
            raise paths.DependencyError("Invalid release candidate record")
        candidate = paths.local_path(root, relative)
        manifest, _ = candidate_materials(candidate, Path(record["build"]), Path(record["sources"]))
        packaging.check_release(candidate)
        print("Prepared release verified; no setup, downloads or archive created.")
        return candidate
    archive = paths.local_path(root, f"dist/modLauncher-{version}-windows-x64.zip")
    if archive.exists() or archive.with_suffix(".zip.sha256").exists():
        raise paths.DependencyError("This release version already exists in dist")
    if build_output is None:
        build_output, source_output = build_inputs(root, version)
    print("Prepare the isolated runtime, dependencies and trusted runner", flush=True)
    candidate = paths.local_path(root, f".cache/releases/windows-{version}-{uuid.uuid4().hex[:8]}")
    prepare_candidate(root, candidate, build_output, source_output)
    manifest, materials = candidate_materials(candidate, build_output, source_output)
    packaging.check_release(candidate)
    record = {"candidate": candidate.relative_to(root).as_posix(),
              "build": str(build_output.resolve()), "sources": str(source_output.resolve())}
    temporary = paths.local_path(root, f".cache/releases/.record-{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(receipt)
    if prepare_only:
        print(f"Release candidate ready: {candidate}; no ZIP created.")
        return candidate
    result = packaging.package_release(candidate, version, manifest, materials, output_root=root)
    print(f"ZIP and SHA-256 ready: {result}")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check-only", action="store_true")
    action.add_argument("--prepare-only", action="store_true", help="build and verify a candidate without making a ZIP")
    parser.add_argument("--frida-build", type=Path)
    parser.add_argument("--frida-sources", type=Path)
    args = parser.parse_args(argv)
    try:
        run(ROOT, args.version, check_only=args.check_only, prepare_only=args.prepare_only,
            build_output=args.frida_build.absolute() if args.frida_build else None,
            source_output=args.frida_sources.absolute() if args.frida_sources else None)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f"Release failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
