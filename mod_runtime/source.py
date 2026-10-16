# SPDX-License-Identifier: MPL-2.0
"""Validate source-only Python manifests and snapshot bounded source as inert text."""

from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import PurePosixPath
import re
import stat
import tomllib

from . import _source_files as disk
from ._source_policy import MAX_SOURCE_BYTES, MAX_SOURCE_FILES, module_part
from .contract import HOST_OPERATIONS

MAX_TREE_ENTRIES = 512
MAX_DEPTH = 16
SOURCE_API_VERSION = "0.2.0"
_FIELDS = {"schema", "id", "version", "api", "runtime", "entry", "capabilities"}
_NUMBER = r"(?:0|[1-9][0-9]*)"
_PRE = rf"(?:{_NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-{_PRE}(?:\.{_PRE})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"


class SourceError(ValueError):
    """A source manifest or tree is unsupported, unsafe, changed or over budget."""


def read_manifest(folder):
    """Return a pinned ordinary folder's path and bounded, inert manifest bytes."""
    try:
        with ExitStack() as stack:
            root, parent = disk.root_directory(stack, folder)
            return root, disk.read_file(root / "mod.toml", parent, 16 * 1024)
    except (OSError, ValueError, TypeError) as error:
        raise SourceError("Cannot read mod manifest: " + str(error)[:256]) from error


@dataclass(frozen=True)
class SourceManifest:
    """Schema 2 metadata; source entry modules replace author component paths."""

    id: str
    version: str
    api: str
    entry: str
    capabilities: frozenset[str]

    @classmethod
    def parse(cls, content):
        """Parse bounded UTF-8 TOML without importing or compiling author code."""
        try:
            if type(content) is not bytes or len(content) > 16 * 1024:
                raise SourceError("Expected at most 16 KiB of manifest bytes")
            data = tomllib.loads(content.decode("utf-8"))
            if set(data) != _FIELDS or type(data["schema"]) is not int or data["schema"] != 2:
                raise SourceError("Expected source manifest schema 2 and its documented fields")
            if type(data["id"]) is not str or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", data["id"]):
                raise SourceError("Expected a short named mod identifier")
            if type(data["version"]) is not str or len(data["version"]) > 64 or not re.fullmatch(_VERSION, data["version"]):
                raise SourceError("Expected a short SemVer version")
            if data["api"] != SOURCE_API_VERSION or data["runtime"] != "python":
                raise SourceError("Unsupported source API version or runtime profile")
            entry = data["entry"]
            if type(entry) is not str or not 0 < len(entry) <= 128:
                raise SourceError("Expected a short dotted entry module")
            for part in entry.split("."):
                module_part(part)
            capabilities = data["capabilities"]
            known = {spec.capability for spec in HOST_OPERATIONS.values()}
            if (type(capabilities) is not list
                or any(type(name) is not str or name not in known for name in capabilities)
                or len(capabilities) != len(set(capabilities))):
                raise SourceError("Unknown or duplicate capability")
            return cls(data["id"], data["version"], data["api"], entry, frozenset(capabilities))
        except SourceError:
            raise
        except (ValueError, TypeError, RecursionError) as error:
            raise SourceError("Cannot parse source manifest") from error


@dataclass(frozen=True)
class SourceSnapshot:
    """An immutable ordered copy of a manifest and UTF-8 Python source files."""

    manifest: SourceManifest
    files: tuple[tuple[str, str], ...]
    total_bytes: int

    @classmethod
    def read(cls, folder):
        """Read only mod.toml and src/; no build tools, imports or author Wasm."""
        try:
            with ExitStack() as stack:
                root, parent = disk.root_directory(stack, folder)
                manifest = SourceManifest.parse(disk.read_file(root / "mod.toml", parent, 16 * 1024))
                files, names, entries = {}, set(), 0
                total = 0

                def collect(path, descriptor, prefix=()):
                    nonlocal entries, total
                    before = disk.stamp(os.fstat(descriptor))
                    # scandir(fd) is POSIX-only; Windows ancestors remain pinned
                    # with handles that deny write/delete sharing.
                    with os.scandir(path if os.name == "nt" else descriptor) as children:
                        listed = []
                        for child in children:
                            entries += 1
                            if entries > MAX_TREE_ENTRIES:
                                raise SourceError("Source tree exceeds its entry limit")
                            info = os.stat(path / child.name, follow_symlinks=False) if os.name == "nt" else child.stat(follow_symlinks=False)
                            listed.append((child.name, info))
                    for name, info in sorted(listed):
                        parts = (*prefix, name)
                        relative = "/".join(parts)
                        if len(parts) > MAX_DEPTH or len(relative) > 256:
                            raise SourceError("Source path exceeds its depth or length limit")
                        if relative.casefold() in names:
                            raise SourceError("Case-ambiguous source paths")
                        names.add(relative.casefold())
                        if stat.S_ISDIR(info.st_mode):
                            module_part(name)
                            with disk.opened(path / name, descriptor, directory=True) as child:
                                if disk.stamp(os.fstat(child)) != disk.stamp(info):
                                    raise SourceError("Source directory changed before scanning")
                                collect(path / name, child, parts)
                        else:
                            if not name.endswith(".py"):
                                raise SourceError("Only Python source files are supported in src")
                            module_part(name[:-3])
                            if len(files) >= MAX_SOURCE_FILES:
                                raise SourceError("Source tree exceeds its file limit")
                            content = disk.read_file(path / name, descriptor, MAX_SOURCE_BYTES - total, info)
                            total += len(content)
                            files[relative] = content.decode("utf-8-sig")
                    if before != disk.stamp(os.fstat(descriptor)):
                        raise SourceError("Source directory changed during scanning")

                with disk.opened(root / "src", parent, directory=True) as source:
                    collect(root / "src", source)
                if manifest.entry.replace(".", "/") + ".py" not in files:
                    raise SourceError("Entry module is missing")
                modules = set()
                for name in files:
                    path = PurePosixPath(name)
                    for ancestor in path.parents:
                        if ancestor != PurePosixPath(".") and (ancestor / "__init__.py").as_posix() not in files:
                            raise SourceError("Source packages require __init__.py")
                    package = path.name == "__init__.py" and path.parent != PurePosixPath(".")
                    module = (path.parent if package else path.with_suffix("")).as_posix().casefold()
                    if module in modules:
                        raise SourceError("Ambiguous source module and package")
                    modules.add(module)
                return cls(manifest, tuple(sorted(files.items())), total)
        except SourceError:
            raise
        except (OSError, ValueError, TypeError, RecursionError) as error:
            raise SourceError("Cannot snapshot mod source: " + str(error)[:256]) from error
