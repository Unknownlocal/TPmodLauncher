# SPDX-License-Identifier: MPL-2.0
"""Read bounded runner bundles and verify the exact bytes returned to the caller.

The receipt is an integrity inventory, not publisher authentication. Its trust
comes from the launcher distribution, just like the existing dependency manifest.
No manifest, source file or Wasm component is executed here.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

ABI_VERSION = "0.4.0"
COMPONENTIZE_VERSION = "0.25.1"
EXPORTS = MappingProxyType({
    "put-source": (("string", "string"), None),
    "load-source": (("string",), "string"),
    "activate": ((), None),
    "on-event": (("string", "string"), "string"),
    "bind-regulation": (("u32", "string"), "string"),
    "begin-regulation-policy": (("u64", "u64", "u32"), "string"),
    "put-regulation-policy": (("string",), None),
    "seal-regulation-policy-manifest": ((), "string"),
    "install-regulation-records": (("string",), "string"),
    "begin-regulation-record": (("u32", "u32"), None),
    "put-regulation-record": (("string",), None),
    "seal-regulation-record": ((), "string"),
    "seal-regulation-policy": ((), "string"),
    "commit-regulation-policy": (("u64", "u64"), "string"),
    "reset-regulation-career": (("u64",), "string"),
    "begin-regulation-action": (("u64", "u64", "u64", "u32", "string"), "string"),
    "put-regulation-action": (("string",), None),
    "seal-regulation-action": ((), "string"),
    "prepare-regulation-slots": (("u64",), "string"),
    "validate-resident-regulation": (("u64", "u32"), "string"),
    "discard-regulation-action": (("u64",), None),
})
SOURCES = ("source/runner.wit", "source/guest.py", "source/_source_policy.py",
           "source/_regulations/__init__.py", "source/_regulations/policy.py",
           "source/_regulations/handlers.py", "source/_regulations/values.py",
           "source/_regulations/transfer.py", "source/_regulations/catalog.py",
           "source/_regulations/action.py", "source/_regulations/resident.py")
NOTICE_FILES = (
    "notices/LICENCE.Project.txt",
    "notices/LICENSE.ComponentizePy.txt",
    "notices/LICENSE.PythonGuest.txt",
    "notices/LICENSE.WasiLibc.txt",
    "notices/LICENSE.LLVM.txt",
    "notices/LICENSE.PythonThirdParty.rst",
    "notices/LICENSE.Expat.txt",
    "notices/LICENSE.Libcxx.txt",
    "notices/LICENSE.Libcxxabi.txt",
    "notices/LICENSE.CompilerRt.txt",
    "notices/LICENSE.Zlib.txt",
    "notices/LICENSE.HACL.txt",
    "notices/LICENSE.HACLProject.txt",
    "notices/LICENSE.Rust.txt",
    "notices/COPYRIGHT.RustStd.html",
    "notices/LICENSE.Unicode.txt",
    "notices/LICENSE.WitDylibMIT.txt",
    "notices/LICENSE.WitDylibApache.txt",
    "notices/LICENSE.WasiLibcApache.txt",
    "notices/LICENSE.WasiLibcApacheLLVM.txt",
    "notices/LICENSE.WasiLibcMIT.txt",
    "notices/LICENSE.WasiCloudlibc.txt",
    "notices/LICENSE.Musl.txt",
    "notices/LICENSE.MuslFts.txt",
    "notices/LICENSE.Anyhow.txt",
    "notices/LICENSE.NumBigint.txt",
    "notices/LICENSE.NumInteger.txt",
    "notices/LICENSE.NumTraits.txt",
    "notices/LICENSE.OnceCell.txt",
    "notices/LICENSE.PyO3.txt",
    "notices/LICENSE.PyO3Ffi.txt",
    "notices/LICENSE.RustLibc.txt",
    "notices/LICENSE.PortableAtomic.txt",
    "notices/LICENSE.WitBindgen.txt",
    "notices/LICENSE.WitBindgenLLVM.txt",
    "notices/NOTICE.HaclHeaders.txt",
    "notices/NOTICE.Libmpdec.txt",
    "notices/NOTICE.CPythonNative.txt",
    "notices/NOTICE.WasiLibcSources.txt",
    "notices/NOTICE.Dlmalloc.txt",
    "notices/NOTICE.SQLite.txt",
    "notices/NOTICE.PythonChanges.txt",
)
FILES = frozenset((*SOURCES, *NOTICE_FILES, "runner.wasm"))
# Nested trusted packages are allowed only where the fixed inventory needs them.
DIRECTORIES = frozenset(parent.as_posix() for name in FILES for parent in Path(name).parents if parent != Path("."))
MANIFEST = "bundle.json"
DIRECTORY = "libs/mod-runners"
MAX_COMPONENT_BYTES = 32 * 1024 * 1024
_HEADER = b"\0asm\x0d\0\x01\0"


class BundleError(ValueError):
    """A runner bundle is unsupported, incomplete or differs from its receipt."""


def _checked(path):
    """Reject linked paths before resolution, including linked parent directories."""
    path = Path(path).absolute()
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)):
        raise BundleError("Runner bundle paths must not be linked")
    return path


def _read(path, maximum):
    """Read a bounded ordinary file; nonblocking opens cannot hang on POSIX FIFOs."""
    path = _checked(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
                raise BundleError("Expected a bounded, ordinary, unshared bundle file")
            data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise BundleError("Runner bundle file exceeds its size limit")
            return data
    except OSError as error:
        raise BundleError(f"Cannot read runner bundle file: {path.name}") from error


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise BundleError("Duplicate runner receipt field")
        result[name] = value
    return result


class RunnerBundle:
    """Verify a trusted runner bundle without a compiler or mod permissions."""

    def __init__(self, directory):
        self.directory = _checked(directory)
        try:
            self._receipt = _read(self.directory / MANIFEST, 64 * 1024)
            receipt = json.loads(self._receipt, object_pairs_hook=_unique_object)
            if (
                type(receipt) is not dict
                or set(receipt) != {"format", "abi", "componentize_py", "files"}
                or type(receipt["format"]) is not int or receipt["format"] != 1
                or receipt["abi"] != ABI_VERSION
                or receipt["componentize_py"] != COMPONENTIZE_VERSION
                or type(receipt["files"]) is not dict or set(receipt["files"]) != FILES
            ):
                raise BundleError("Unsupported or incomplete runner receipt")
            for name, record in receipt["files"].items():
                if (
                    type(record) is not dict or set(record) != {"size", "sha256"}
                    or type(record["size"]) is not int or not 0 < record["size"] <= MAX_COMPONENT_BYTES
                    or type(record["sha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
                ):
                    raise BundleError("Invalid runner file receipt")
            # Retain a private copy; callers cannot change the accepted inventory.
            self._records = receipt["files"]
            self.verify()
        except BundleError:
            raise
        except (OSError, TypeError, ValueError, RecursionError) as error:
            raise BundleError("Cannot read runner receipt") from error

    def _file(self, name):
        record = self._records[name]
        data = _read(self.directory / name, record["size"])
        if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise BundleError(f"Runner bundle file differs from receipt: {name}")
        if name.endswith(".wasm") and not data.startswith(_HEADER):
            raise BundleError("Expected a portable WebAssembly Component")
        return data

    def verify(self):
        """Check the complete bounded inventory, including sources and preserved notices."""
        found = set()
        for parent, directories, files in self.directory.walk(follow_symlinks=False):
            for name in directories + files:
                item = _checked(parent / name)
                relative = item.relative_to(self.directory).as_posix()
                if item.is_dir():
                    if relative not in DIRECTORIES:
                        raise BundleError("Unexpected runner bundle directory")
                else:
                    found.add(relative)
                    if relative not in FILES | {MANIFEST}:
                        raise BundleError("Unexpected runner bundle file")
        if found != FILES | {MANIFEST}:
            raise BundleError("Incomplete runner bundle inventory")
        for name in FILES:
            self._file(name)

    def component(self):
        """Return newly verified portable bytes; the host binds per-mod permissions."""
        return self._file("runner.wasm")

    def copy_to(self, directory):
        """Copy the accepted inventory into a fresh release folder, receipt last."""
        directory = _checked(directory)
        directory.mkdir(parents=True)
        for name in sorted(FILES):
            data = self._file(name)
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as destination:
                destination.write(data)
        with (directory / MANIFEST).open("xb") as destination:
            destination.write(self._receipt)
        return RunnerBundle(directory)
