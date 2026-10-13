# SPDX-License-Identifier: MPL-2.0
"""Prepare bundled Python and packages from the configured archives.

Read pyproject.toml and pylock.toml, verify downloaded inputs, and install packages
into libs/vendor. Check existing output folders before reusing them."""

import argparse
import contextlib
import importlib.metadata
import importlib.util
import hashlib
import json
import re
import tomllib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile

from packaging.markers import default_environment
from packaging.pylock import Package, PackageWheel, Pylock, PylockSelectError, PylockValidationError
from packaging.tags import compatible_tags, cpython_tags

# Load the helper beside this script, regardless of the current working folder.
_spec = importlib.util.spec_from_file_location("_tpm_bundled_dependencies", Path(__file__).with_name("bundled_dependencies.py"))
dependencies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dependencies)


def read_settings(root: Path) -> dict:
    # root is the project folder; interpreter and build-tool settings belong in pyproject.toml.
    try:
        data = dependencies.local_path(root, "pyproject.toml").read_bytes()
        settings = tomllib.loads(data.decode("utf-8"))["tool"]["modlauncher"]
        dependencies.validate_runtime(settings["runtime"])
        # Keep pip's generated files consistent between developer and CI builds.
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", settings["build"]["pip_version"]):
            raise ValueError("invalid release pip version")
        return settings
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise dependencies.DependencyError(f"Cannot read build settings: {error}") from error


def target_environment(runtime: dict) -> dict:
    # runtime gives the included Python version; evaluate package rules for Windows x64.
    version = runtime["version"]
    environment = default_environment()
    environment.update(python_version=".".join(version.split(".")[:2]), python_full_version=version,
                       implementation_name="cpython", implementation_version=version,
                       platform_python_implementation="CPython", os_name="nt", sys_platform="win32",
                       platform_system="Windows", platform_machine="AMD64", extra="")
    return environment


def read_lock(root: Path, runtime: dict) -> list[tuple[Package, PackageWheel]]:
    # Read the standard package lock and select wheels for the included Python, not the build Python.
    try:
        data = dependencies.local_path(root, "pylock.toml").read_bytes()
        lock = Pylock.from_dict(tomllib.loads(data.decode("utf-8")))
        version = tuple(int(part) for part in runtime["version"].split(".")[:2])
        interpreter = "cp" + "".join(str(part) for part in version)
        # Include this Python's compiled wheels, older stable-ABI wheels and Python-only wheels.
        tags = [*cpython_tags(python_version=version, abis=[interpreter], platforms=["win_amd64"]),
                *compatible_tags(python_version=version, interpreter=interpreter, platforms=["win_amd64"])]
        selected = list(lock.select(environment=target_environment(runtime), tags=tags,
                                    extras=(), dependency_groups=()))
        # This release tool installs wheels only; it never builds or runs package source.
        if not selected or any(not isinstance(wheel, PackageWheel) or package.version is None
                               for package, wheel in selected):
            raise ValueError("the release requires version-pinned wheels")
        for package, wheel in selected:
            # Keep local installation inputs inside the checked wheel cache.
            if wheel.path != ".cache/wheels/" + wheel.filename:
                raise ValueError(f"wheel path must be .cache/wheels/{wheel.filename}")
            dependencies.local_path(root, wheel.path)
            # The standard permits other hashes; our authentication checks require SHA-256.
            if not re.fullmatch(r"[0-9a-f]{64}", wheel.hashes.get("sha256", "")):
                raise ValueError(f"invalid wheel SHA-256: {package.name}")
        return selected
    except (OSError, ValueError, KeyError, TypeError, PylockValidationError, PylockSelectError) as error:
        raise dependencies.DependencyError(f"Cannot read dependency lock: {error}") from error


@contextlib.contextmanager
def build_directory(parent: Path, prefix: str):
    """Yield a temporary folder under parent and remove it when the with block ends.

    The folder inherits its parent's normal permissions so other users can read files
    copied into it. TemporaryDirectory can give files private Windows permissions."""
    # parent holds temporary build folders; prefix names the build type, with a unique ending.
    parent = parent.resolve()
    path = parent / (prefix + uuid.uuid4().hex)
    path.mkdir(mode=0o755)
    try:
        yield path
    finally:
        # Clean up only the temporary folder created here.
        # Check that it is inside parent and has no links before deleting it.
        # Clean up only if the temporary folder still exists.
        if path.exists():
            dependencies.local_path(parent, path.name)
            for _ in dependencies.tree_paths(path):
                pass  # Check paths and links; there is no need to hash files before deleting them.
            # Make sure the folder being deleted is still inside parent.
            if not path.resolve().is_relative_to(parent):
                raise dependencies.DependencyError("Build cleanup escaped its parent.")
            shutil.rmtree(path)


def archive_files(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Check ZIP entries and return ordinary file entries indexed by their relative paths."""
    # archive is the open Python ZIP; check its file paths and return the file entries.
    files = {}
    seen = set()
    total = 0
    for info in archive.infolist():
        name = dependencies.safe_name(info.filename).as_posix()
        # These ZIP attributes tell us whether the entry is a file, folder or link.
        mode = stat.S_IFMT(info.external_attr >> 16)
        # Allow ordinary files and folders only; reject links and password-protected entries.
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.flag_bits & 1:
            raise dependencies.DependencyError(f"Unsupported archive member: {info.filename}")
        # Reject names that Windows would treat as the same file.
        if name.casefold() in seen:
            raise dependencies.DependencyError(f"Duplicate archive path: {name}")
        seen.add(name.casefold())
        # Folder names have been checked; there is no file content to copy for them.
        if info.is_dir():
            continue
        # A folder entry must have a name ending in /.
        if mode == stat.S_IFDIR:
            raise dependencies.DependencyError(f"Invalid directory entry: {name}")
        total += info.file_size
        # Reject ZIPs that would unpack to more than 256 MB.
        if total > 256 * 1024 * 1024:
            raise dependencies.DependencyError("Runtime archive exceeds the supported size.")
        files[name] = info
    folded = {name.casefold() for name in files}
    for name in files:
        # A name cannot be both a file and a folder, such as a alongside a/b.py.
        if any(parent.as_posix().casefold() in folded for parent in dependencies.safe_name(name).parents if str(parent) != "."):
            raise dependencies.DependencyError(f"File/directory collision: {name}")
    return files


def download(url: str, destination: Path, digest: str | None = None) -> None:
    """Download to destination, checking the SHA-256 checksum when digest is provided."""
    # url is the download address; destination is the saved file; digest is its expected checksum.
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reuse a saved download only when an expected checksum was given.
    if digest and destination.exists():
        with destination.open("rb") as source:
            # The saved file matches, so there is no need to download it again.
            if dependencies.digest_stream(source) == digest:
                return
    # Download to a temporary file beside destination, then rename it when complete.
    descriptor, temporary = tempfile.mkstemp(prefix="download-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(url, timeout=60) as response:
            shutil.copyfileobj(response, output, 1024 * 1024)
        with Path(temporary).open("rb") as source:
            # Check the downloaded file before replacing a previously saved copy.
            if digest and dependencies.digest_stream(source) != digest:
                raise dependencies.DependencyError(f"Downloaded artifact SHA-256 mismatch: {destination.name}")
        Path(temporary).replace(destination)
    finally:
        # Remove any leftover temporary download; leave the completed file alone.
        if Path(temporary).exists():
            Path(temporary).unlink()


def prepare_runtime(root: Path, runtime: dict, archive_path: Path) -> None:
    """Check existing Python files against the archive, or extract into a new folder."""
    # root is the build folder; runtime gives the Python settings; archive_path is the checked ZIP.
    target = dependencies.local_path(root, runtime["directory"])
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive_files(archive)
        expected = {}
        for name, info in members.items():
            with archive.open(name) as source:
                expected[name] = (dependencies.digest_stream(source), info.file_size)
        # Check an existing Python folder without replacing files that may be in use.
        if target.exists():
            actual = dependencies.file_inventory(target)
            # Stop if Python files are missing, changed or unexpected; do not overwrite them.
            if actual != expected:
                raise dependencies.DependencyError("Existing Python runtime differs from the verified official archive; it was not overwritten.")
            return
        with build_directory(target.parent, ".python-stage-") as stage:
            # Make sure the temporary extraction folder is inside libs.
            if not stage.is_relative_to(target.parent.resolve()):
                raise dependencies.DependencyError("Runtime staging escaped libs.")
            extracted = stage / "runtime"
            extracted.mkdir()
            for name in members:
                destination = dependencies.local_path(extracted, name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Create each file only if it does not already exist.
                with archive.open(name) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
            extracted.rename(target)


def validate_packages(directory: Path, packages: list[tuple[Package, PackageWheel]], runtime: dict) -> None:
    """Check installed package versions and dependencies for the included Python."""
    # directory holds the installed files; packages lists the selected wheels; runtime gives Python settings.
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    expected = {package.name: package for package, _ in packages}
    installed = {}
    for distribution in importlib.metadata.distributions(path=[str(directory)]):
        name = canonicalize_name(distribution.metadata["Name"])
        # Stop if more than one installed package has the same name.
        if name in installed:
            raise dependencies.DependencyError(f"Duplicate installed distribution: {name}")
        installed[name] = distribution
    # The installed package names must exactly match the selected lock entries.
    if set(installed) != set(expected):
        raise dependencies.DependencyError("Pip's installed distribution set differs from the lock.")
    version = runtime["version"]
    environment = target_environment(runtime)
    for name, distribution in installed.items():
        package = expected[name]
        # The authenticated wheel supplies its own dependency metadata; do not duplicate it in settings.
        if Version(distribution.version) != package.version:
            raise dependencies.DependencyError(f"Installed version differs from the lock: {name}")
        # Stop if the package does not support our included Python version.
        if not SpecifierSet(distribution.metadata.get("Requires-Python", "")).contains(version):
            raise dependencies.DependencyError(f"Package does not support the embedded Python: {name}")
        for text in distribution.requires or []:
            requirement = Requirement(text)
            # Skip required packages whose rules do not apply to our Python version or platform.
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            dependency = installed.get(canonicalize_name(requirement.name))
            # Stop for missing or wrong-version packages, or unsupported download links and optional features.
            if (requirement.url or requirement.extras or dependency is None
                    or not requirement.specifier.contains(dependency.version)):
                raise dependencies.DependencyError(f"Unfulfilled embedded-runtime requirement: {text}")


def package_directory(files: dict) -> str:
    """Return a libs/vendor folder name based on the file paths, hashes and sizes."""
    # files lists paths, checksums and sizes; use them to name the package folder.
    # Sort the file list so identical files always produce the same folder name.
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return "libs/vendor/" + hashlib.sha256(encoded).hexdigest()


def manifest_bytes(runtime: dict, packages: list[tuple[Package, PackageWheel]], files: dict) -> bytes:
    """Create bundled_packages.toml bytes describing the prepared files.

    Include Python settings, package versions, relative file paths, hashes and sizes."""
    # runtime gives Python settings; packages gives versions; files gives checksums and sizes.
    lines = ["# SPDX-License-Identifier: MPL-2.0",
             "# Generated by launcher/prepare_release.py; review alongside dependency changes.",
             "format = 2", "directory = " + json.dumps(package_directory(files)),
             "", "[runtime]", "version = " + json.dumps(runtime["version"]),
             "directory = " + json.dumps(runtime["directory"]), "", "[packages]"]
    for package, _ in sorted(packages, key=lambda item: item[0].name):
        lines.append(json.dumps(package.name) + " = " + json.dumps(str(package.version)))
    for name, (digest, size) in sorted(files.items()):
        # Use JSON quoting to write these file paths safely into TOML.
        lines.extend(["", "[[files]]", "path = " + json.dumps(name),
                      f'sha256 = "{digest}"', f"size = {size}"])
    return ("\n".join(lines) + "\n").encode("utf-8")


def prepare_packages(root: Path, *, refresh_manifest: bool = False) -> Path:
    """Install cached wheels and return the verified package folder under libs/vendor.

    Prepare files in a temporary folder and check them before creating the final folder.
    With refresh_manifest=True, update bundled_packages.toml; otherwise require the
    prepared files to match it. Check existing package folders without overwriting them."""
    # root is the build folder; refresh_manifest allows updating the saved file list.
    settings = read_settings(root)
    runtime = settings["runtime"]
    packages = read_lock(root, runtime)
    # Use the specified pip version so the extra files it creates stay consistent.
    if importlib.metadata.version("pip") != settings["build"]["pip_version"]:
        raise dependencies.DependencyError("Install requirements-dev.txt: the release pip version must match pyproject.toml.")
    wheels = dependencies.local_path(root, ".cache/wheels")
    for _, wheel in packages:
        with dependencies.local_path(root, wheel.path).open("rb") as source:
            # Check each saved wheel again before pip installs it.
            if dependencies.digest_stream(source) != wheel.hashes["sha256"]:
                raise dependencies.DependencyError(f"Build wheel SHA-256 mismatch: {wheel.filename}")
    vendor = dependencies.local_path(root, "libs/vendor")
    vendor.mkdir(parents=True, exist_ok=True)
    manifest = dependencies.local_path(root, "bundled_packages.toml")
    # Use a new temporary folder for this build only.
    # Leave all existing package folders alone.
    with build_directory(vendor, ".package-build-") as build:
        # Make sure the temporary build folder is inside libs/vendor.
        if not build.is_relative_to(vendor.resolve()):
            raise dependencies.DependencyError("Package staging escaped libs/vendor.")
        stage = build / "packages"
        requirements = build / "requirements.txt"
        # Pip currently selects pylock entries for the build Python, even with cross-target flags.
        # Pass our target-selected versions and hashes through its standard requirements format.
        requirements.write_text("\n".join(
            f"{package.name}=={package.version} --hash=sha256:{wheel.hashes['sha256']}"
            for package, wheel in packages) + "\n", encoding="utf-8")
        # Stop user pip settings from changing this install; leave the parent process unchanged.
        environment = {name: value for name, value in os.environ.items() if not name.upper().startswith("PIP_")}
        environment["PIP_CONFIG_FILE"] = os.devnull
        # Install only the downloaded wheels, without internet access or compiling code.
        # Do not use packages already installed on the build computer.
        python_version = ".".join(runtime["version"].split(".")[:2])
        # ABI identifies compatible compiled Python code; Python 3.13 uses cp313.
        abi = "cp" + python_version.replace(".", "")
        # Install the checked wheels for 64-bit Windows into the empty temporary folder.
        subprocess.run([sys.executable, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
                        "install", "--no-index", "--find-links", str(wheels), "--require-hashes",
                        "--only-binary=:all:", "--no-deps", "--ignore-installed", "--no-compile", "--no-cache-dir",
                        "--platform", "win_amd64", "--python-version", python_version, "--implementation", "cp",
                        # Allow wheels for this Python version, compatible older compiled code, or Python-only code.
                        "--abi", abi, "--abi", "abi3", "--abi", "none",
                        "--target", str(stage), "-r", str(requirements)], env=environment, check=True)
        validate_packages(stage, packages, runtime)
        inventory = dependencies.file_inventory(stage)
        # Startup does not read .pth settings files or install command-line tools.
        # Packages must work by loading them directly from our folder.
        # Reject .pth settings, compiled .pyc files and scripts in bin/.
        if any(name.endswith((".pth", ".pyc")) or name.startswith("bin/") for name in inventory):
            raise dependencies.DependencyError("Prepared packages need unsupported site processing or scripts.")
        generated = manifest_bytes(runtime, packages, inventory)
        target = dependencies.local_path(root, package_directory(inventory))
        # Require the saved file list to match unless an update was explicitly requested.
        if not refresh_manifest and (not manifest.exists() or manifest.read_text(encoding="utf-8") != generated.decode("utf-8")):
            raise dependencies.DependencyError("Build differs from bundled_packages.toml; review and use --refresh-manifest for an intentional update.")
        # Reuse an existing package folder only if every file matches.
        if target.exists():
            if dependencies.file_inventory(target) != inventory:
                raise dependencies.DependencyError("Existing package bundle differs from this build; it was not overwritten.")
        else:
            # Another build may create the same package folder first.
            # Check that its files match; never replace or delete that folder.
            # Pip may leave its installed files with restricted access.
            # On Windows with Python 3.15, other users may then be unable to read them.
            # Copy files into new folders with normal access permissions.
            # Copy the file contents, leaving the old permissions behind.
            published = build / "publish"
            shutil.copytree(stage, published, copy_function=shutil.copyfile)
            try:
                published.rename(target)
            except OSError:
                # If another build created the folder first, accept it only when every file matches.
                if not target.exists() or dependencies.file_inventory(target) != inventory:
                    raise
        # Save the new file list only after the package folder is ready; replace it in one step.
        if refresh_manifest:
            descriptor, temporary_manifest = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=root)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(generated)
                os.replace(temporary_manifest, manifest)
            finally:
                Path(temporary_manifest).unlink(missing_ok=True)
    # Check the finished packages using the same checks as startup.
    return dependencies.verify_bundle(root)


def main(argv=None) -> int:
    """Prepare release dependencies and return 0 on success or 1 on failure."""
    # argv can supply command-line arguments; None reads them normally; return 0 on success.
    parser = argparse.ArgumentParser(description="Authenticate inputs and prepare bundled runtime packages.")
    parser.add_argument("--refresh-manifest", action="store_true",
                        help="write the package hash manifest for an intentional, reviewed dependency change")
    arguments = parser.parse_args(argv)
    # Find the build folder beside this script, regardless of the current working folder.
    root = Path(__file__).resolve().parent.parent
    try:
        settings = read_settings(root)
        runtime = settings["runtime"]
        packages = read_lock(root, runtime)
        cache = dependencies.local_path(root, ".cache/downloads")
        archive = cache / runtime["filename"]
        download(runtime["url"], archive, runtime["sha256"])
        bundle = cache / (runtime["filename"] + ".sigstore")
        download(runtime["url"] + ".sigstore", bundle)
        # Check who signed the Python ZIP and who confirmed their identity.
        subprocess.run([sys.executable, "-m", "sigstore", "verify", "identity",
                        "--bundle", str(bundle), "--cert-identity", runtime["identity"],
                        "--cert-oidc-issuer", runtime["issuer"], str(archive)], check=True)
        prepare_runtime(root, runtime, archive)
        for package, wheel in packages:
            # A publishing identity is required before accepting a downloaded package for release.
            repositories = {identity.get("repository") for identity in package.attestation_identities or []
                            if identity.get("kind") == "GitHub"}
            if len(repositories) != 1:
                raise dependencies.DependencyError(f"Expected one GitHub publishing identity: {package.name}")
            repository = repositories.pop()
            if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
                raise dependencies.DependencyError(f"Invalid publishing repository: {package.name}")
            # Release preparation downloads from the upstream URL; pip later uses the local cache path.
            if not wheel.url or not wheel.url.startswith("https://"):
                raise dependencies.DependencyError(f"Expected an HTTPS wheel source: {package.name}")
            destination = dependencies.local_path(root, wheel.path)
            download(wheel.url, destination, wheel.hashes["sha256"])
            provenance = cache / (wheel.filename + ".provenance.json")
            url = f"https://pypi.org/integrity/{package.name}/{package.version}/{wheel.filename}/provenance"
            download(url, provenance)
            # Check the signed publishing record links this wheel to the expected source repository.
            subprocess.run([sys.executable, "-m", "pypi_attestations", "verify", "pypi",
                            "--repository", "https://github.com/" + repository,
                            "--provenance-file", str(provenance), str(destination)], check=True)
        directory = prepare_packages(root, refresh_manifest=arguments.refresh_manifest)
        print(f"Release inputs authenticated; distributable packages ready in {directory.relative_to(root).as_posix()}.")
        return 0
    # Return an error if a download check, build step or package install fails.
    except (OSError, ValueError, KeyError, TypeError, dependencies.DependencyError,
            subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(f"Release preparation failed: {error}", file=sys.stderr)
        return 1


# Run the build command only when this file is started directly.
if __name__ == "__main__":
    raise SystemExit(main())
