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
from packaging.pylock import (
    Package,
    PackageWheel,
    Pylock,
    PylockSelectError,
    PylockValidationError,
)
from packaging.tags import compatible_tags, cpython_tags

# Load the helper beside this script, regardless of the current working folder.
_spec = importlib.util.spec_from_file_location(
    "_tpm_bundled_dependencies", Path(__file__).with_name("bundled_dependencies.py")
)
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
        raise dependencies.DependencyError(
            f"Cannot read build settings: {error}"
        ) from error


def target_environment(runtime: dict) -> dict:
    # runtime gives the included Python version; evaluate package rules for Windows x64.
    version = runtime["version"]
    environment = default_environment()
    environment.update(
        python_version=".".join(version.split(".")[:2]),
        python_full_version=version,
        implementation_name="cpython",
        implementation_version=version,
        platform_python_implementation="CPython",
        os_name="nt",
        sys_platform="win32",
        platform_system="Windows",
        platform_machine="AMD64",
        extra="",
    )
    return environment


def read_lock(
    root: Path, runtime: dict, lockfile: str = "pylock.toml"
) -> list[tuple[Package, PackageWheel]]:
    # lockfile selects a standard lock inside root; evaluate it for the included Python.
    try:
        data = dependencies.local_path(root, lockfile).read_bytes()
        lock = Pylock.from_dict(tomllib.loads(data.decode("utf-8")))
        version = tuple(int(part) for part in runtime["version"].split(".")[:2])
        interpreter = "cp" + "".join(str(part) for part in version)
        # Include this Python's compiled wheels, older stable-ABI wheels and Python-only wheels.
        tags = [
            *cpython_tags(
                python_version=version, abis=[interpreter], platforms=["win_amd64"]
            ),
            *compatible_tags(
                python_version=version, interpreter=interpreter, platforms=["win_amd64"]
            ),
        ]
        selected = list(
            lock.select(
                environment=target_environment(runtime),
                tags=tags,
                extras=(),
                dependency_groups=(),
            )
        )
        # This release tool installs wheels only; it never builds or runs package source.
        if not selected or any(
            not isinstance(wheel, PackageWheel) or package.version is None
            for package, wheel in selected
        ):
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
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        PylockValidationError,
        PylockSelectError,
    ) as error:
        raise dependencies.DependencyError(
            f"Cannot read dependency lock: {error}"
        ) from error


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
            raise dependencies.DependencyError(
                f"Unsupported archive member: {info.filename}"
            )
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
            raise dependencies.DependencyError(
                "Runtime archive exceeds the supported size."
            )
        files[name] = info
    folded = {name.casefold() for name in files}
    for name in files:
        # A name cannot be both a file and a folder, such as a alongside a/b.py.
        if any(
            parent.as_posix().casefold() in folded
            for parent in dependencies.safe_name(name).parents
            if str(parent) != "."
        ):
            raise dependencies.DependencyError(f"File/directory collision: {name}")
    return files


def download(
    url: str, destination: Path, digest: str | None = None, *, hash_name="sha256"
) -> None:
    """Download to destination, checking its hash; SHA-256 is the default."""
    # digest is the expected hex checksum; npm source locks use SHA-512 instead of the default SHA-256.
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reuse a saved download only when an expected checksum was given.
    if digest and destination.exists():
        with destination.open("rb") as source:
            # The saved file matches, so there is no need to download it again.
            if hashlib.file_digest(source, hash_name).hexdigest() == digest:
                return
    # Download to a temporary file beside destination, then rename it when complete.
    descriptor, temporary = tempfile.mkstemp(
        prefix="download-", suffix=".tmp", dir=destination.parent
    )
    try:
        with (
            os.fdopen(descriptor, "wb") as output,
            urllib.request.urlopen(url, timeout=60) as response,
        ):
            shutil.copyfileobj(response, output, 1024 * 1024)
        with Path(temporary).open("rb") as source:
            # Check the downloaded file before replacing a previously saved copy.
            if digest and hashlib.file_digest(source, hash_name).hexdigest() != digest:
                raise dependencies.DependencyError(
                    f"Downloaded artifact {hash_name.upper().replace('SHA', 'SHA-')} mismatch: {destination.name}"
                )
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
                raise dependencies.DependencyError(
                    "Existing Python runtime differs from the verified official archive; it was not overwritten."
                )
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


def validate_packages(
    directory: Path, packages: list[tuple[Package, PackageWheel]], runtime: dict
) -> None:
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
            raise dependencies.DependencyError(
                f"Duplicate installed distribution: {name}"
            )
        installed[name] = distribution
    # The installed package names must exactly match the selected lock entries.
    if set(installed) != set(expected):
        raise dependencies.DependencyError(
            "Pip's installed distribution set differs from the lock."
        )
    version = runtime["version"]
    environment = target_environment(runtime)
    for name, distribution in installed.items():
        package = expected[name]
        # The authenticated wheel supplies its own dependency metadata; do not duplicate it in settings.
        if Version(distribution.version) != package.version:
            raise dependencies.DependencyError(
                f"Installed version differs from the lock: {name}"
            )
        # Stop if the package does not support our included Python version.
        if not SpecifierSet(distribution.metadata.get("Requires-Python", "")).contains(
            version
        ):
            raise dependencies.DependencyError(
                f"Package does not support the embedded Python: {name}"
            )
        for text in distribution.requires or []:
            requirement = Requirement(text)
            # Skip required packages whose rules do not apply to our Python version or platform.
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            dependency = installed.get(canonicalize_name(requirement.name))
            # Stop for missing or wrong-version packages, or unsupported download links and optional features.
            if (
                requirement.url
                or requirement.extras
                or dependency is None
                or not requirement.specifier.contains(dependency.version)
            ):
                raise dependencies.DependencyError(
                    f"Unfulfilled embedded-runtime requirement: {text}"
                )


def package_directory(files: dict) -> str:
    """Return a libs/vendor folder name based on the file paths, hashes and sizes."""
    # files lists paths, checksums and sizes; use them to name the package folder.
    # Sort the file list so identical files always produce the same folder name.
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return "libs/vendor/" + hashlib.sha256(encoded).hexdigest()


def manifest_bytes(
    runtime: dict, packages: list[tuple[Package, PackageWheel]], files: dict
) -> bytes:
    """Create bundled_packages.toml bytes describing the prepared files.

    Include Python settings, package versions, relative file paths, hashes and sizes."""
    # runtime gives Python settings; packages gives versions; files gives checksums and sizes.
    lines = [
        "# SPDX-License-Identifier: MPL-2.0",
        "# Generated by launcher/prepare_release.py; review alongside dependency changes.",
        "format = 2",
        "directory = " + json.dumps(package_directory(files)),
        "",
        "[runtime]",
        "version = " + json.dumps(runtime["version"]),
        "directory = " + json.dumps(runtime["directory"]),
        "",
        "[packages]",
    ]
    for package, _ in sorted(packages, key=lambda item: item[0].name):
        lines.append(
            json.dumps(package.name) + " = " + json.dumps(str(package.version))
        )
    for name, (digest, size) in sorted(files.items()):
        # Use JSON quoting to write these file paths safely into TOML.
        lines.extend(
            [
                "",
                "[[files]]",
                "path = " + json.dumps(name),
                f'sha256 = "{digest}"',
                f"size = {size}",
            ]
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def prune_vendor(root: Path) -> Path | None:
    """Remove obsolete generated folders and return the manifest-selected path, if any."""
    # root is the launcher folder; retain and verify the selected bundle when it exists.
    selected = None
    if dependencies.local_path(root, "bundled_packages.toml").exists():
        manifest = dependencies.read_manifest(root)
        selected = dependencies.local_path(root, manifest["directory"])
        if selected.exists():
            dependencies.verify_bundle(root)
    vendor = dependencies.local_path(root, "libs/vendor")
    # A first installation may have neither a manifest nor a vendor folder yet.
    if not vendor.exists():
        return selected
    obsolete = []
    for item in sorted(vendor.iterdir()):
        # Older builds also used a content hash followed by a random 32-character suffix.
        # Other names, including another build's temporary folder, are not ours to remove.
        if item == selected or not re.fullmatch(
            r"[0-9a-f]{64}(?:-[0-9a-f]{32})?", item.name
        ):
            continue
        candidate = dependencies.local_path(root, "libs/vendor/" + item.name)
        # Require an ordinary folder directly inside vendor, never a redirected path.
        if not candidate.is_dir() or candidate.resolve().parent != vendor.resolve():
            raise dependencies.DependencyError(
                f"Cannot clean obsolete package folder: {item.name}"
            )
        for _ in dependencies.tree_paths(candidate):
            pass  # Check every path for links before deleting any obsolete folder.
        obsolete.append(candidate)
    for candidate in obsolete:
        # Check the absolute target again immediately before recursive deletion.
        dependencies.local_path(root, "libs/vendor/" + candidate.name)
        if candidate.resolve().parent != vendor.resolve():
            raise dependencies.DependencyError("Vendor cleanup escaped libs/vendor.")
        try:
            shutil.rmtree(candidate)
        except OSError as error:
            # A running launcher may hold its old Frida DLL open on Windows.
            raise dependencies.DependencyError(
                f"Cannot remove obsolete package folder {candidate.name}; "
                "close any running launcher and retry release preparation."
            ) from error
    return selected


def prepare_packages(
    root: Path, *, refresh_manifest: bool = False, lockfile: str = "pylock.toml"
) -> Path:
    """Install cached wheels and return the verified package folder under libs/vendor.

    Prepare files in a temporary folder and check them before creating the final folder.
    With refresh_manifest=True, update bundled_packages.toml; otherwise require the
    prepared files to match it. Prune obsolete folders first and reuse a matching verified
    bundle without pip. An explicit manifest refresh rebuilds it from cached wheels.
    Run preparation while the launcher is closed.
    """
    # lockfile selects the package profile; refresh_manifest updates the active file list.
    settings = read_settings(root)
    runtime = settings["runtime"]
    packages = read_lock(root, runtime, lockfile)
    # Free obsolete copies before creating installation staging; retain the current bundle.
    selected = prune_vendor(root)
    if not refresh_manifest and selected is not None and selected.exists():
        manifest = dependencies.read_manifest(root)
        expected_packages = {
            package.name: str(package.version) for package, _ in packages
        }
        expected_runtime = {key: runtime[key] for key in ("version", "directory")}
        # The files already passed verification; reuse only the requested versions and runtime.
        if (
            manifest["packages"] == expected_packages
            and manifest["runtime"] == expected_runtime
        ):
            return selected
    # Use the specified pip version so the extra files it creates stay consistent.
    if importlib.metadata.version("pip") != settings["build"]["pip_version"]:
        raise dependencies.DependencyError(
            "Install requirements-dev.txt: the release pip version must match pyproject.toml."
        )
    wheels = dependencies.local_path(root, ".cache/wheels")
    for _, wheel in packages:
        with dependencies.local_path(root, wheel.path).open("rb") as source:
            # Check each saved wheel again before pip installs it.
            if dependencies.digest_stream(source) != wheel.hashes["sha256"]:
                raise dependencies.DependencyError(
                    f"Build wheel SHA-256 mismatch: {wheel.filename}"
                )
    vendor = dependencies.local_path(root, "libs/vendor")
    vendor.mkdir(parents=True, exist_ok=True)
    manifest = dependencies.local_path(root, "bundled_packages.toml")
    # Use a new temporary folder for this build only.
    # Keep the selected bundle until the replacement has passed all checks.
    with build_directory(vendor, ".package-build-") as build:
        # Make sure the temporary build folder is inside libs/vendor.
        if not build.is_relative_to(vendor.resolve()):
            raise dependencies.DependencyError("Package staging escaped libs/vendor.")
        stage = build / "packages"
        requirements = build / "requirements.txt"
        # Pip currently selects pylock entries for the build Python, even with cross-target flags.
        # Pass our target-selected versions and hashes through its standard requirements format.
        requirements.write_text(
            "\n".join(
                f"{package.name}=={package.version} --hash=sha256:{wheel.hashes['sha256']}"
                for package, wheel in packages
            )
            + "\n",
            encoding="utf-8",
        )
        # Stop user pip settings from changing this install; leave the parent process unchanged.
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.upper().startswith("PIP_")
        }
        environment["PIP_CONFIG_FILE"] = os.devnull
        # Install only the downloaded wheels, without internet access or compiling code.
        # Do not use packages already installed on the build computer.
        python_version = ".".join(runtime["version"].split(".")[:2])
        # ABI identifies compatible compiled Python code; Python 3.13 uses cp313.
        abi = "cp" + python_version.replace(".", "")
        # Install the checked wheels for 64-bit Windows into the empty temporary folder.
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "pip",
                "--isolated",
                "--disable-pip-version-check",
                "install",
                "--no-index",
                "--find-links",
                str(wheels),
                "--require-hashes",
                "--only-binary=:all:",
                "--no-deps",
                "--ignore-installed",
                "--no-compile",
                "--no-cache-dir",
                "--platform",
                "win_amd64",
                "--python-version",
                python_version,
                "--implementation",
                "cp",
                # Allow wheels for this Python version, compatible older compiled code, or Python-only code.
                "--abi",
                abi,
                "--abi",
                "abi3",
                "--abi",
                "none",
                "--target",
                str(stage),
                "-r",
                str(requirements),
            ],
            env=environment,
            check=True,
        )
        validate_packages(stage, packages, runtime)
        inventory = dependencies.file_inventory(stage)
        # Startup does not read .pth settings files or install command-line tools.
        # Packages must work by loading them directly from our folder.
        # Reject .pth settings, compiled .pyc files and scripts in bin/.
        if any(
            name.endswith((".pth", ".pyc")) or name.startswith("bin/")
            for name in inventory
        ):
            raise dependencies.DependencyError(
                "Prepared packages need unsupported site processing or scripts."
            )
        generated = manifest_bytes(runtime, packages, inventory)
        target = dependencies.local_path(root, package_directory(inventory))
        # Require the saved file list to match unless an update was explicitly requested.
        if not refresh_manifest and (
            not manifest.exists()
            or manifest.read_text(encoding="utf-8") != generated.decode("utf-8")
        ):
            raise dependencies.DependencyError(
                "Build differs from bundled_packages.toml; review and use --refresh-manifest for an intentional update."
            )
        # Reuse an existing package folder only if every file matches.
        if target.exists():
            if dependencies.file_inventory(target) != inventory:
                raise dependencies.DependencyError(
                    "Existing package bundle differs from this build; it was not overwritten."
                )
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
                if (
                    not target.exists()
                    or dependencies.file_inventory(target) != inventory
                ):
                    raise
        # Save the new file list only after the package folder is ready; replace it in one step.
        if refresh_manifest:
            descriptor, temporary_manifest = tempfile.mkstemp(
                prefix="manifest-", suffix=".tmp", dir=root
            )
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(generated)
                os.replace(temporary_manifest, manifest)
            finally:
                Path(temporary_manifest).unlink(missing_ok=True)
    # After an update succeeds, the previously selected bundle can also be removed.
    # Startup itself stays read-only.
    directory = dependencies.verify_bundle(root)
    if selected is not None and selected != directory:
        prune_vendor(root)
    return directory


def authenticate_runtime(root: Path, runtime: dict) -> Path:
    """Download the pinned Python ZIP, verify its signing identity and return its path."""
    cache = dependencies.local_path(root, ".cache/downloads")
    archive = cache / runtime["filename"]
    download(runtime["url"], archive, runtime["sha256"])
    bundle = cache / (runtime["filename"] + ".sigstore")
    download(runtime["url"] + ".sigstore", bundle)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "sigstore",
            "verify",
            "identity",
            "--bundle",
            str(bundle),
            "--cert-identity",
            runtime["identity"],
            "--cert-oidc-issuer",
            runtime["issuer"],
            str(archive),
        ],
        check=True,
    )
    return archive


def prepare_reduced_release(
    root: Path,
    build_output: Path,
    output: str,
    repository: str,
    source_digest: str,
    source_output: Path | None = None,
) -> Path:
    """Prepare an isolated reduced candidate from a workflow-authenticated Windows build."""
    # These expected identities come from the caller, never from downloaded build details.
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or ""
    ) or not re.fullmatch(r"[0-9a-f]{40}", source_digest or ""):
        raise dependencies.DependencyError(
            "Supply --repository owner/repo and --source-digest full-commit-ID"
        )
    destination = dependencies.local_path(root, output)
    # Keep candidates in local release staging; never replace the active source/runtime tree.
    if (
        not destination.is_relative_to(root.resolve() / ".cache/releases")
        or destination == root.resolve() / ".cache/releases"
    ):
        raise dependencies.DependencyError(
            "Reduced output must be a folder below .cache/releases"
        )
    if destination.exists():
        raise dependencies.DependencyError(
            "Use a fresh reduced output folder; existing candidates are preserved"
        )
    # Load the sibling build verifier for script and package execution alike.
    spec = importlib.util.spec_from_file_location(
        "_reduced_build", Path(__file__).with_name("build_frida.py")
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    recipe = build.read_recipe()
    try:
        wheel = build.verify_output(build_output, recipe, "windows-x86_64")
    except RuntimeError as error:
        raise dependencies.DependencyError(str(error)) from error
    details = (build_output / "build-details.json").read_bytes()
    digest = json.loads(details)["wheel"]["sha256"]
    if source_output is None:
        raise dependencies.DependencyError(
            "Reduced preparation requires --frida-sources matching this build"
        )
    spec = importlib.util.spec_from_file_location(
        "_release_sources", Path(__file__).with_name("frida_sources.py")
    )
    sources = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sources)
    try:
        source_archive = sources.verify(source_output, build_output)
    except RuntimeError as error:
        raise dependencies.DependencyError(str(error)) from error
    source_details = (source_output / "source-details.json").read_bytes()
    # Verify both the binary and its source/configuration record with GitHub's standard verifier.
    # Pin the workflow, launcher source commit and hosted-runner policy, not just a repository name.
    for artifact in (
        wheel,
        build_output / "build-details.json",
        source_archive,
        source_output / "source-details.json",
    ):
        try:
            subprocess.run(
                [
                    "gh",
                    "attestation",
                    "verify",
                    str(artifact),
                    "--repo",
                    repository,
                    "--signer-workflow",
                    repository + "/.github/workflows/reduced-frida.yml",
                    "--source-digest",
                    source_digest,
                    "--signer-digest",
                    source_digest,
                    "--deny-self-hosted-runners",
                ],
                check=True,
            )
        except FileNotFoundError as error:
            raise dependencies.DependencyError(
                "Reduced preparation needs GitHub CLI (gh) installed and able to access the expected repository."
            ) from error
    if (
        (build_output / "build-details.json").read_bytes() != details
        or build.file_record(wheel)["sha256"] != digest
        or (source_output / "source-details.json").read_bytes() != source_details
    ):
        raise dependencies.DependencyError(
            "Reduced build changed during authentication"
        )
    sources.verify(source_output, build_output)
    settings = read_settings(root)
    # Keep the launcher's other locked dependencies when replacing only its Frida build.
    companions = [
        (package, companion)
        for package, companion in read_lock(root, settings["runtime"])
        if package.name != "frida"
    ]
    prepare_wheels(root, companions)
    archive = authenticate_runtime(root, settings["runtime"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with build_directory(destination.parent, ".reduced-stage-") as stage:
        # Reuse existing preparation; this candidate has its own standard lock and manifest.
        shutil.copyfile(root / "pyproject.toml", stage / "pyproject.toml")
        cached = dependencies.local_path(stage, ".cache/wheels/" + wheel.name)
        cached.parent.mkdir(parents=True)
        shutil.copyfile(wheel, cached)
        lock = "\n".join(
            [
                'lock-version = "1.0"',
                'created-by = "modLauncher reduced build"',
                'requires-python = "==3.13.*"',
                """environments = ["sys_platform == 'win32' and platform_machine == 'AMD64'"]""",
                "extras = []",
                "dependency-groups = []",
                "",
                "[[packages]]",
                'name = "frida"',
                "version = " + json.dumps(recipe["frida"]["package_version"]),
                'requires-python = ">=3.7"',
                "",
                "[[packages.wheels]]",
                "name = " + json.dumps(wheel.name),
                "path = " + json.dumps(".cache/wheels/" + wheel.name),
                "hashes = {sha256 = " + json.dumps(digest) + "}",
                "",
                "[[packages.attestation-identities]]",
                'kind = "GitHub"',
                "repository = " + json.dumps(repository),
                "",
            ]
        )
        for package, companion in companions:
            shutil.copyfile(
                dependencies.local_path(root, companion.path),
                dependencies.local_path(stage, companion.path),
            )
            # Write the selected wheel and its checks into the candidate's standard lock.
            lock += "\n".join(
                [
                    "\n[[packages]]",
                    "name = " + json.dumps(package.name),
                    "version = " + json.dumps(str(package.version)),
                    "requires-python = "
                    + json.dumps(str(package.requires_python or "")),
                    "[[packages.wheels]]",
                    "name = " + json.dumps(companion.filename),
                    "path = " + json.dumps(companion.path),
                    "hashes = {sha256 = "
                    + json.dumps(companion.hashes["sha256"])
                    + "}",
                    *(["url = " + json.dumps(companion.url)] if companion.url else []),
                    "",
                ]
            )
            for identity in package.attestation_identities or []:
                lock += "\n".join(
                    [
                        "[[packages.attestation-identities]]",
                        "kind = " + json.dumps(identity["kind"]),
                        "repository = " + json.dumps(identity["repository"]),
                        "",
                    ]
                )
        (stage / "pylock.toml").write_text(lock, encoding="utf-8")
        prepare_runtime(stage, settings["runtime"], archive)
        prepare_packages(stage, refresh_manifest=True)
        # Retain the signed build record alongside existing licences and the usable launcher source.
        for directory in ("launcher", "hook_manager", "notices"):
            shutil.copytree(
                root / directory,
                stage / directory,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                copy_function=shutil.copyfile,
            )
        for name in ("LICENCE", "README.md"):
            shutil.copyfile(root / name, stage / name)
        (stage / "build-details.json").write_bytes(details)
        source_destination = stage / "source"
        source_destination.mkdir()
        shutil.copyfile(source_archive, source_destination / source_archive.name)
        (source_destination / "source-details.json").write_bytes(source_details)
        sources.verify(source_destination, stage)
        # Recheck the copied bytes against the authenticated build before publishing any candidate.
        if build.file_record(cached)["sha256"] != digest:
            raise dependencies.DependencyError(
                "Reduced wheel changed during preparation"
            )
        stage.rename(destination)
    return destination


def prepare_wheels(root: Path, packages: list[tuple[Package, PackageWheel]]) -> None:
    """Fetch locked wheels and verify any publishing identities declared in the lock."""
    # Wheels without an identity get a hash check, not a publisher-authentication claim.
    for package, wheel in packages:
        identities = package.attestation_identities or []
        if wheel.url is None:
            # Local wheels are hash-checked by prepare_packages, including authenticated builds.
            continue
        if not wheel.url.startswith("https://"):
            raise dependencies.DependencyError(
                f"Expected an HTTPS wheel source: {package.name}"
            )
        # An unsupported identity must fail rather than silently lose its signature check.
        if identities and (
            len(identities) != 1
            or identities[0].get("kind") != "GitHub"
            or not isinstance(identities[0].get("repository"), str)
            or not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", identities[0]["repository"]
            )
        ):
            raise dependencies.DependencyError(
                f"Expected one GitHub publishing identity: {package.name}"
            )
        destination = dependencies.local_path(root, wheel.path)
        download(wheel.url, destination, wheel.hashes["sha256"])
        if not identities:
            continue
        provenance = dependencies.local_path(
            root, ".cache/downloads/" + wheel.filename + ".provenance.json"
        )
        url = f"https://pypi.org/integrity/{package.name}/{package.version}/{wheel.filename}/provenance"
        download(url, provenance)
        # Signature failure is fatal; it must never fall back to a hash-only check.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pypi_attestations",
                "verify",
                "pypi",
                "--repository",
                "https://github.com/" + identities[0]["repository"],
                "--provenance-file",
                str(provenance),
                str(destination),
            ],
            check=True,
        )


def main(argv=None) -> int:
    """Prepare dependencies and return 0 on success or 1 on failure."""
    # argv can supply command-line arguments; None reads them normally; return 0 on success.
    parser = argparse.ArgumentParser(
        description="Prepare bundled packages; check hashes and declared publishing identities."
    )
    parser.add_argument(
        "--lockfile",
        default="pylock.toml",
        help="package lock inside the project; use pylock.stock.toml for the official fallback",
    )
    parser.add_argument(
        "--refresh-manifest",
        action="store_true",
        help="write the package hash manifest for an intentional, reviewed dependency change",
    )
    parser.add_argument(
        "--frida-build",
        type=Path,
        help="authenticate a reduced build into a separate candidate",
    )
    parser.add_argument(
        "--frida-sources",
        type=Path,
        help="checked source archive/details directory matching the reduced build",
    )
    parser.add_argument(
        "--repository", help="expected GitHub repository that built the reduced wheel"
    )
    parser.add_argument(
        "--source-digest",
        help="expected launcher commit that produced the reduced build",
    )
    parser.add_argument(
        "--output",
        help="fresh reduced candidate folder below .cache/releases; defaults to reduced",
    )
    arguments = parser.parse_args(argv)
    # Find the build folder beside this script, regardless of the current working folder.
    root = Path(__file__).resolve().parent.parent
    try:
        if arguments.frida_build:
            if arguments.lockfile != "pylock.toml":
                raise dependencies.DependencyError(
                    "Authenticated candidates use their own build-derived lock."
                )
            candidate = prepare_reduced_release(
                root,
                arguments.frida_build.absolute(),
                arguments.output or ".cache/releases/reduced",
                arguments.repository,
                arguments.source_digest,
                arguments.frida_sources.absolute() if arguments.frida_sources else None,
            )
            print(
                f"Reduced Frida build authenticated; candidate ready in {candidate.relative_to(root).as_posix()}; distribution and live acceptance remain separate gates."
            )
            return 0
        if (
            arguments.repository
            or arguments.source_digest
            or arguments.output
            or arguments.frida_sources
        ):
            raise dependencies.DependencyError(
                "Reduced provenance options require --frida-build"
            )
        settings = read_settings(root)
        runtime = settings["runtime"]
        packages = read_lock(root, runtime, arguments.lockfile)
        archive = authenticate_runtime(root, runtime)
        prepare_runtime(root, runtime, archive)
        prepare_wheels(root, packages)
        directory = prepare_packages(
            root,
            refresh_manifest=arguments.refresh_manifest,
            lockfile=arguments.lockfile,
        )
        print(
            f"Package hashes checked; packages ready in {directory.relative_to(root).as_posix()}."
        )
        return 0
    # Return an error if a download check, build step or package install fails.
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as error:
        print(f"Release preparation failed: {error}", file=sys.stderr)
        return 1


# Run the build command only when this file is started directly.
if __name__ == "__main__":
    raise SystemExit(main())
