# SPDX-License-Identifier: MPL-2.0
"""Assemble a Windows player ZIP from prepared inputs, on Windows or WSL.

This command is offline. It verifies and packages existing artifacts; it never
downloads dependencies, builds Frida, starts the game or publishes a release.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
WINDOWS_HOST = os.name == "nt"
_spec = importlib.util.spec_from_file_location(
    "_package_sources", Path(__file__).with_name("frida_sources.py")
)
sources = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sources)
build = sources.build
release = build.release
paths = release.dependencies

SOURCE_DIRECTORIES = ("launcher", "hook_manager", "mod_runtime", "examples")
SOURCE_SUFFIXES = {".py", ".js", ".md", ".toml", ".wit", ".sh"}
ROOT_FILES = ("README.md", "LICENCE", "ATTRIBUTION.txt", "bundled_packages.toml")


def resolve_inputs(root, build_output=None, source_output=None):
    """Find retained artifacts for the exact wheel selected by the package lock."""
    settings = release.read_settings(root)["runtime"]
    selected = release.read_lock(root, settings)
    wheel_pin = next(wheel for package, wheel in selected if package.name == "frida")
    cache = paths.local_path(root, ".cache/reduced-frida")
    recipe = build.read_recipe(root / "launcher/frida_build.toml")
    candidates = [build_output] if build_output else sorted(
        record.parent for record in cache.glob("*/build-details.json")
    )
    matched = None
    for candidate in candidates:
        try:
            wheel = build.verify_output(candidate, recipe, "windows-x86_64")
            if build.file_record(wheel)["sha256"] == wheel_pin.hashes["sha256"]:
                matched = candidate
                break
        except (OSError, ValueError, RuntimeError, KeyError, zipfile.BadZipFile):
            if build_output:
                raise
    if matched is None:
        raise paths.DependencyError(
            "No retained Windows Frida build matches pylock.toml. Restore the pinned "
            "wheel/build-details pair under .cache/reduced-frida/<build>/, or pass "
            "-FridaBuild to Package-Release.ps1. A fresh native rebuild may have a "
            "different hash and must be selected as an intentional dependency update."
        )
    source_candidates = [source_output] if source_output else [
        record.parent for record in sorted(
            cache.glob("*/source-details.json"),
            key=lambda item: (item.stat().st_mtime_ns, str(item)), reverse=True,
        )
    ]
    for candidate in source_candidates:
        try:
            sources.verify(candidate, matched)
            return {"build": str(matched.resolve()), "sources": str(candidate.resolve()),
                    "wheel": str(wheel.resolve()),
                    "wheel_cache": str(paths.local_path(root, wheel_pin.path))}
        except (OSError, ValueError, RuntimeError, KeyError):
            if source_output:
                raise
    raise paths.DependencyError(
        "No matching Frida source export was retained. Restore its source archive "
        "and source-details.json under .cache/reduced-frida/<sources>/, or pass "
        "-FridaSources to Package-Release.ps1."
    )


def helper_bytes(runtime):
    """Use the manifest's exact interpreter path, without a system-Python fallback."""
    interpreter = runtime["directory"].replace("/", "\\") + "\\python.exe"
    return (
        "@echo off\r\n"
        "setlocal DisableDelayedExpansion\r\n"
        f'"%~dp0{interpreter}" -B "%~dp0launcher\\steam_launch_options.py"\r\n'
        'set "result=%errorlevel%"\r\n'
        "pause\r\n"
        "exit /b %result%\r\n"
    ).encode("ascii")


def linux_helper_bytes():
    """Provide a native Linux helper in the same Windows-runtime ZIP."""
    return (
        '#!/usr/bin/env bash\n'
        '# SPDX-License-Identifier: MPL-2.0\n'
        'set -euo pipefail\n'
        'mod_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)\n'
        'exec bash "$mod_root/launcher/steam_launch_options.sh"\n'
    ).encode("ascii")


def verify_inputs(root, build_output, source_output):
    """Check prepared dependencies, Python archive, runner and matching Frida source."""
    manifest = paths.read_manifest(root)
    vendor = paths.verify_bundle(root)
    settings = release.read_settings(root)["runtime"]
    if manifest["runtime"] != {key: settings[key] for key in ("version", "directory")}:
        raise paths.DependencyError("The prepared runtime differs from pyproject.toml")
    runtime = paths.local_path(root, settings["directory"])
    # Compare every runtime file with the already pinned archive; no download or repair.
    archive = paths.local_path(root, ".cache/downloads/" + settings["filename"])
    with archive.open("rb") as stream:
        if paths.digest_stream(stream) != settings["sha256"]:
            raise paths.DependencyError("Cached Python archive differs from its pinned hash")
    if not runtime.is_dir():
        raise paths.DependencyError("Prepare the bundled Python before packaging")
    release.prepare_runtime(root, settings, archive)
    artifacts = release.runner_artifacts()
    artifacts.RunnerBundle(root / artifacts.DIRECTORY)
    recipe = build.read_recipe(root / "launcher/frida_build.toml")
    wheel = build.verify_output(build_output, recipe, "windows-x86_64")
    if manifest["packages"]["frida"] != recipe["frida"]["package_version"]:
        raise paths.DependencyError("This packager expects the prepared reduced Frida profile")
    # Check the actual installed payload, not only a matching version or native binary.
    with zipfile.ZipFile(wheel) as package:
        for name in release.archive_files(package):
            if name.endswith(".dist-info/RECORD"):
                continue  # pip rewrites RECORD when installing the wheel.
            with package.open(name) as upstream, paths.local_path(vendor, name).open("rb") as installed:
                if paths.digest_stream(upstream) != paths.digest_stream(installed):
                    raise paths.DependencyError("Installed Frida differs from the supplied build")
    source_archive = sources.verify(source_output, build_output)
    materials = {
        "source/frida/" + wheel.name: wheel,
        "source/frida/build-details.json": paths.local_path(build_output, "build-details.json"),
        "source/frida/" + source_archive.name: source_archive,
        "source/frida/source-details.json": paths.local_path(source_output, "source-details.json"),
    }
    return manifest, materials


def collect_files(root, manifest, materials):
    """Select only release content; never walk the whole development/game folder."""
    files = {name: paths.local_path(root, name) for name in ROOT_FILES}
    for directory in SOURCE_DIRECTORIES:
        base = paths.local_path(root, directory)
        if not base.is_dir():
            raise paths.DependencyError(f"Missing source directory: {directory}")
        for item in paths.tree_paths(base):
            if item.is_file() and "__pycache__" not in item.parts and item.suffix in SOURCE_SUFFIXES:
                files[item.relative_to(root).as_posix()] = item
    # These inventories must remain complete, including upstream notices and metadata.
    for directory in ("notices", manifest["runtime"]["directory"],
                      manifest["directory"], "libs/mod-runners"):
        base = paths.local_path(root, directory)
        for name in paths.file_inventory(base):
            files[f"{directory}/{name}"] = paths.local_path(base, name)
    files.update(materials)
    folded = set()
    for name, item in files.items():
        paths.safe_name(name)
        if name.casefold() in folded or not item.is_file():
            raise paths.DependencyError(f"Missing or ambiguous release file: {name}")
        folded.add(name.casefold())
    return files


def check_release(root):
    """Run real native imports through the extracted Windows interpreter, even in WSL."""
    manifest = paths.read_manifest(root)
    interpreter = paths.local_path(root, manifest["runtime"]["directory"] + "/python.exe")
    entry = root / "launcher/entry.py"
    if not WINDOWS_HOST:
        # Windows Python needs a Windows script path, not /mnt/c/... or /tmp/....
        try:
            entry = subprocess.run(
                ["wslpath", "-w", str(entry)], check=True, capture_output=True,
                text=True, timeout=15,
            ).stdout.strip()
        except FileNotFoundError as error:
            raise paths.DependencyError("Windows bundle verification requires Windows or WSL with Windows interop") from error
    result = subprocess.run(
        [str(interpreter), "-B", str(entry), "--check-only"],
        check=True, capture_output=True, text=True, timeout=90,
    )
    print(result.stdout.strip())


def write_zip(destination, files, generated):
    """Produce stable file ordering, metadata and compression for identical inputs."""
    if set(files) & set(generated):
        raise paths.DependencyError("Generated release filenames collide with input files")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(set(files) | set(generated)):
            info = zipfile.ZipInfo("modLauncher/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            with archive.open(info, "w", force_zip64=True) as target:
                if name in generated:
                    target.write(generated[name])
                else:
                    with files[name].open("rb") as source:
                        shutil.copyfileobj(source, target, 1024 * 1024)


def package_release(root, version, manifest, materials, *, checker=None, output_root=None):
    """Publish a fresh local ZIP/checksum only after extracting and checking it."""
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}", version):
        raise paths.DependencyError("Version must contain 1-64 letters, digits, dots, underscores or hyphens")
    output = paths.local_path(output_root or root, "dist")
    output.mkdir(exist_ok=True)
    destination = output / f"modLauncher-{version}-windows-x64.zip"
    checksum = destination.with_suffix(".zip.sha256")
    if destination.exists() or checksum.exists():
        raise paths.DependencyError("This version already exists in dist; use a new version or move the old artifacts")
    files = collect_files(root, manifest, materials)
    generated = {"SteamLaunchOptions.cmd": helper_bytes(manifest["runtime"]),
                 "SteamLaunchOptions.sh": linux_helper_bytes(),
                 "VERSION.txt": (version + "\n").encode("ascii")}
    # All temporary files belong to this fresh staging folder inside the checked dist directory.
    with tempfile.TemporaryDirectory(prefix=".package-", dir=output) as temporary:
        stage = Path(temporary)
        archive_path = stage / destination.name
        expected = {name: (build.file_record(item)["sha256"], item.stat().st_size)
                    for name, item in files.items()}
        expected.update({name: (hashlib.sha256(data).hexdigest(), len(data))
                         for name, data in generated.items()})
        write_zip(archive_path, files, generated)
        extracted = stage / "extracted"
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(extracted)  # All names were selected locally and checked above.
        root_copy = extracted / "modLauncher"
        if paths.file_inventory(root_copy) != expected:
            raise paths.DependencyError("Extracted ZIP differs from the selected release files")
        (checker or check_release)(root_copy)
        digest = build.file_record(archive_path)["sha256"]
        # Exclusive creation preserves existing output even if another build raced this one.
        created = []
        try:
            with destination.open("xb") as target:
                created.append(destination)
                with archive_path.open("rb") as source:
                    shutil.copyfileobj(source, target, 1024 * 1024)
            with checksum.open("x", encoding="ascii", newline="\n") as target:
                created.append(checksum)
                target.write(f"{digest}  {destination.name}\n")
        except BaseException:
            for item in created:
                item.unlink()
            raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release label, e.g. 0.1.0")
    parser.add_argument("--frida-build", type=Path, help="override automatic pinned Windows build discovery")
    parser.add_argument("--frida-sources", type=Path, help="override automatic matching source discovery")
    parser.add_argument("--resolve-inputs", action="store_true", help="print discovered input paths as JSON without preparing or packaging")
    parser.add_argument("--check-only", action="store_true", help="verify prepared inputs and native imports without making a ZIP")
    arguments = parser.parse_args(argv)
    try:
        inputs = resolve_inputs(
            ROOT, arguments.frida_build.absolute() if arguments.frida_build else None,
            arguments.frida_sources.absolute() if arguments.frida_sources else None,
        )
        if arguments.resolve_inputs:
            print(json.dumps(inputs))
            return 0
        manifest, materials = verify_inputs(ROOT, Path(inputs["build"]), Path(inputs["sources"]))
        if arguments.check_only:
            collect_files(ROOT, manifest, materials)
            check_release(ROOT)
            print("Packaging inputs ready; no release ZIP created.")
        else:
            destination = package_release(ROOT, arguments.version, manifest, materials)
            print(f"ZIP and SHA-256 ready: {destination}")
        print("Archive checks do not replace the existing Frida distribution review or confirm Proton/Wine compatibility.")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f"Packaging failed: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            if error.stdout:
                print(error.stdout, file=sys.stderr)
            if error.stderr:
                print(error.stderr, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
