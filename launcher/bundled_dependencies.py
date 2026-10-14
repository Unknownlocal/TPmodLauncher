# SPDX-License-Identifier: MPL-2.0
"""Check the bundled package files and load Frida using the included Python.

bundled_packages.toml lists the expected files, hashes and sizes.
These checks read existing files without installing or downloading packages."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
from pathlib import Path, PurePosixPath
import re
import struct
import sys
import tomllib


class DependencyError(RuntimeError):
    """Raised when dependency settings, files or imports fail their checks."""


def digest_stream(stream) -> str:
    """Return the SHA-256 checksum of the remaining bytes in an open binary file."""
    # stream is an open file; start reading where it is currently positioned.
    digest = hashlib.sha256()
    # Read a little at a time until the file ends; leave it open for the caller.
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def safe_name(name: str) -> PurePosixPath:
    """Return a relative path, rejecting unsafe names and Windows special filenames."""
    # name is a file path from the ZIP or file list; use / between folders.
    # Reject empty paths, backslashes and colons, which have special meanings on Windows.
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise DependencyError(f"Unsupported path: {name!r}")
    path = PurePosixPath(name)
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{x}" for x in range(1, 10)),
        *(f"lpt{x}" for x in range(1, 10)),
    }
    # Reject paths outside the folder, reserved Windows names and unsafe characters.
    if path.is_absolute() or any(
        part in ("", ".", "..")
        or part.endswith((" ", "."))
        or part.split(".", 1)[0].lower() in reserved
        or any(ord(char) < 32 or char in '<>"|?*' for char in part)
        for part in name.rstrip("/").split("/")
    ):
        raise DependencyError(f"Unsafe path: {name!r}")
    return path


def local_path(root: Path, relative: str) -> Path:
    """Return a path inside root, rejecting unsafe names and links along the path."""
    # root is the project folder; relative names a file or folder inside it.
    root = root.resolve()
    path = root
    for part in safe_name(relative).parts:
        path /= part
        # Check each folder for links that could point somewhere else.
        if path.is_symlink() or path.is_junction():
            raise DependencyError(
                f"Linked bundle path requires separate inspection: {path}"
            )
    # Keep this checked path; resolving it again could hide a link.

    if not path.is_relative_to(root):
        raise DependencyError(f"Bundle path leaves the project: {relative}")
    return path


def validate_runtime(runtime: dict) -> None:
    """Require Python 3.13 or newer and a Python folder directly inside libs."""
    # runtime contains the Python version and folder from the settings file.
    # Require a full Python 3 version, such as 3.13.16.
    if not re.fullmatch(r"3\.\d+\.\d+", runtime["version"]):
        raise ValueError("invalid Python version")
    # We support Python 3.13 or newer.
    if tuple(map(int, runtime["version"].split("."))) < (3, 13, 0):
        raise ValueError("Python 3.13 or newer is required")
    path = safe_name(runtime["directory"])
    # The Python folder must be directly inside libs, with a consistently written path.
    if (
        path.as_posix() != runtime["directory"]
        or len(path.parts) != 2
        or path.parts[0] != "libs"
    ):
        raise ValueError("runtime must be directly under libs")


def read_manifest(root: Path) -> dict:
    """Read bundled_packages.toml and check its format, folders and package versions."""
    # root is launcher folder containing bundled_packages.toml.
    try:
        manifest = tomllib.loads(
            local_path(root, "bundled_packages.toml").read_text(encoding="utf-8")
        )
        # Require file format 2 as a number; true and false are not valid format numbers.
        if type(manifest["format"]) is not int or manifest["format"] != 2:
            raise ValueError("unsupported package manifest")
        validate_runtime(manifest["runtime"])
        directory = safe_name(manifest["directory"])
        # The package folder must be directly inside libs/vendor.
        if (
            directory.as_posix() != manifest["directory"]
            or len(directory.parts) != 3
            or directory.parts[:2] != ("libs", "vendor")
        ):
            raise ValueError("packages must be directly under libs/vendor")
        # Require at least one package, with a valid name and a specific version.
        if not manifest["packages"] or any(
            not re.fullmatch(r"[a-z][a-z0-9_.-]*", name)
            or not re.fullmatch(r"[0-9][a-zA-Z0-9.!+_-]*", version)
            for name, version in manifest["packages"].items()
        ):
            raise ValueError("invalid package versions")
        return manifest
    # Report missing, unreadable or invalid settings as a startup error.
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise DependencyError(
            f"Cannot read bundled package manifest: {error} "
            "Restore the complete launcher release."
        ) from error


def tree_paths(directory: Path):
    """Yield files and folders, stopping if a path is linked or cannot be read."""

    # directory is the folder to check; return its files and folders without following links.
    def unreadable(error):
        # error explains why a folder could not be read; stop rather than skip it.
        raise error

    for parent, directories, files in directory.walk(on_error=unreadable):
        for name in directories + files:
            item = parent / name
            # Reject links before reading the folders they point to.
            if item.is_symlink() or item.is_junction():
                raise DependencyError(f"Linked package path: {item}")
            yield item


def file_inventory(directory: Path) -> dict[str, tuple[str, int]]:
    """Return each file's relative path, SHA-256 checksum and size in bytes."""
    # directory is the folder to check; record each file path, checksum and size in bytes.
    # The package folder must exist and must not be a link to another folder.
    if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
        raise DependencyError(
            f"Missing or linked bundled package directory: {directory}"
        )
    files = {}
    folded = set()
    for item in tree_paths(directory):
        # Calculate checksums for files; folders have already been checked for links.
        if item.is_file():
            name = safe_name(item.relative_to(directory).as_posix()).as_posix()
            # Reject names that differ only in letter case; Windows may treat them as one file.
            if name.casefold() in folded:
                raise DependencyError(f"Ambiguous package path: {name}")
            folded.add(name.casefold())
            with item.open("rb") as source:
                files[name] = (digest_stream(source), item.stat().st_size)
    return files


def _verify_files(root: Path, manifest: dict) -> Path:
    """Check manifest file paths, hashes and sizes; return the matching package folder."""
    # root is the launcher folder; manifest is the file list read by read_manifest.
    try:
        expected = {}
        folded = set()
        for record in manifest["files"]:
            name = safe_name(record["path"]).as_posix()
            # Reject duplicate paths and paths written in more than one way.
            if name != record["path"] or name.casefold() in folded:
                raise ValueError("duplicate/noncanonical manifest path")
            folded.add(name.casefold())
            # Require a SHA-256 checksum and a file size of zero or more; true/false are not sizes.
            if (
                not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                or type(record["size"]) is not int
                or record["size"] < 0
            ):
                raise ValueError("invalid manifest digest/size")
            expected[name] = (record["sha256"], record["size"])
        # An empty file list cannot show that the packages are complete.
        if not expected:
            raise ValueError("empty package manifest")
        directory = local_path(root, manifest["directory"])
        # Check all files, including missing, changed and unexpected ones.
        if file_inventory(directory) != expected:
            raise DependencyError("Bundled packages differ from bundled_packages.toml.")
        return directory
    except (OSError, ValueError, KeyError, TypeError, DependencyError) as error:
        raise DependencyError(
            f"Cannot verify bundled dependencies: {error} "
            "Restore the complete launcher release; startup does not install or repair packages."
        ) from error


def verify_bundle(root: Path) -> Path:
    """Read bundled_packages.toml, check all package files and return their folder."""
    # root is the launcher folder; return the checked package folder without changing files.
    return _verify_files(root, read_manifest(root))


def require_embedded_runtime(root: Path, manifest: dict) -> None:
    """Check that the running interpreter matches the Python specified in the manifest.

    Require its python.exe, exact version and Windows x64 platform, with isolation
    flags that disable environment settings and site-package imports."""
    # root is the launcher folder; manifest gives the required Python version and location.
    expected = local_path(root, manifest["runtime"]["directory"] + "/python.exe")
    # Require 64-bit Windows, the specified Python version and our included python.exe.
    # Python must ignore user settings and packages installed elsewhere.
    if (
        sys.platform != "win32"
        or struct.calcsize("P") != 8
        or sys.version_info[:3]
        != tuple(map(int, manifest["runtime"]["version"].split(".")))
        or Path(sys.executable).resolve() != expected
        or not (
            sys.flags.isolated and sys.flags.ignore_environment and sys.flags.no_site
        )
    ):
        raise DependencyError(
            f"Use this project's {expected.relative_to(root).as_posix()}; "
            f"startup requires its isolated Windows x64 Python {manifest['runtime']['version']} runtime."
        )


def import_frida(root: Path):
    """Check the bundled files, load Frida from their folder and return the imported module."""
    # root is the launcher folder; check Frida and return it for the launcher to use.
    manifest = read_manifest(root)
    require_embedded_runtime(root, manifest)
    directory = _verify_files(root, manifest)
    runtime = local_path(root, manifest["runtime"]["directory"])
    # Stop if Frida was already loaded, since it may have come from another folder.
    if any(name == "frida" or name.startswith("frida.") for name in sys.modules):
        raise DependencyError(
            "Frida was imported before controlled bundle verification."
        )
    sys.dont_write_bytecode = (
        True  # Loading packages must not create compiled Python files.
    )
    # Keep only the included Python folders; remove paths to other installed packages.
    stdlib = [
        path
        for path in sys.path
        if path and Path(path).resolve().is_relative_to(runtime)
    ]
    sys.path[:] = [str(directory), *stdlib]
    try:
        module = importlib.import_module("frida")
        native = importlib.import_module("frida._frida")
        expected = manifest["packages"]["frida"]
        # A rebuilt wheel has a local suffix; Core still reports the upstream version.
        # Check the full installed package version as well as the native Core version.
        if (
            importlib.metadata.version("frida") != expected
            or module.__version__ != expected.partition("+")[0]
            or not Path(module.__file__).resolve().is_relative_to(directory)
            or not Path(native.__file__).resolve().is_relative_to(directory)
        ):
            raise DependencyError(
                "Frida did not load from the verified bundled directory."
            )
        return module
    except (ImportError, OSError, KeyError) as error:
        raise DependencyError(f"Cannot import bundled Frida: {error}") from error
