# SPDX-License-Identifier: MPL-2.0
"""Keep Wasmtime's native cache under the trusted launcher, never in mod content."""

import ctypes
import errno
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading

from wasmtime import WasmtimeError

from launcher.bundled_dependencies import local_path, tree_paths


def _windows_security(path):
    """Read the owner and Windows access rules without changing either."""
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    read = security.GetFileSecurityW
    read.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    read.restype = ctypes.c_int
    size = ctypes.c_uint32()
    # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION; query the buffer size first.
    read(str(path), 5, None, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(size.value)
    if not read(str(path), 5, descriptor, size.value, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    convert = security.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    )
    convert.restype = ctypes.c_int
    text = ctypes.c_void_p()
    if not convert(descriptor, 1, 5, ctypes.byref(text), None):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.wstring_at(text)
    finally:
        kernel.LocalFree(text)


def _private_directory(path):
    """Require a real directory private to this user; never repair untrusted permissions."""
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or path.is_junction():
        raise ValueError("Cache directory is linked or not a directory")
    if os.name == "nt":
        # Python 3.13+ creates 0700 directories with access only for this user and administrators.
        # Compare with a fresh reference rather than inventing a separate Windows ACL policy.
        with tempfile.TemporaryDirectory(
            dir=path.parent, prefix=".cache-access-"
        ) as reference:
            expected = _windows_security(Path(reference))
            # Filesystems without real ACL support must not pass by returning two empty rulesets.
            if "D:P(" not in expected or _windows_security(path) != expected:
                raise ValueError(
                    "Cache owner/access rules differ from a private directory"
                )
    elif info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Cache must belong to this user with private permissions")
    elif sys.platform == "darwin":
        _macos_private_acl(path)


def _macos_private_acl(path):
    """Refuse extended macOS access rules that Unix mode bits alone cannot describe."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.acl_get_file.argtypes = (ctypes.c_char_p, ctypes.c_int)
    libc.acl_get_file.restype = ctypes.c_void_p
    libc.acl_get_entry.argtypes = (
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
    )
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_free.argtypes = (ctypes.c_void_p,)
    libc.acl_free.restype = ctypes.c_int
    acl = libc.acl_get_file(os.fsencode(path), 0x100)  # ACL_TYPE_EXTENDED
    if not acl:
        raise OSError(ctypes.get_errno(), "Cannot read cache access rules")
    try:
        entry = ctypes.c_void_p()
        # Darwin returns 0 for an entry, and -1/EINVAL for the end of a valid ACL.
        ctypes.set_errno(0)
        if libc.acl_get_entry(acl, 0, ctypes.byref(entry)) == 0:  # ACL_FIRST_ENTRY
            raise ValueError("Extended cache access rules require separate inspection")
        if ctypes.get_errno() != errno.EINVAL:
            raise OSError(ctypes.get_errno(), "Cannot inspect cache access rules")
    finally:
        libc.acl_free(acl)


class CompilationCache:
    """Supply a checked cache configuration; unavailable caching must not prevent play."""

    def __init__(self, launcher_root):
        # The entry point supplies this trusted source root; no mod chooses a cache path.
        self.root = Path(launcher_root).resolve(strict=True)
        self.lock = threading.Lock()

    def configure(self, config):
        """Enable caching on this new Wasmtime Config, or leave its cold-compile default."""
        try:
            with self.lock:
                parent = local_path(self.root, "bin")
                parent.mkdir(mode=0o700, exist_ok=True)
                folder = local_path(self.root, "bin/wasmtime-cache")
                folder.mkdir(mode=0o700, exist_ok=True)
                _private_directory(folder)
                # Refuse redirected or nonregular native inputs before Wasmtime can open them.
                for count, item in enumerate(tree_paths(folder), 1):
                    if count > 16_384:
                        raise ValueError("Cache exceeds the inspection entry limit")
                    try:
                        info = item.lstat()
                    except FileNotFoundError:
                        continue  # Wasmtime may replace its own statistics/temporary files.
                    if not stat.S_ISDIR(info.st_mode) and (
                        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    ):
                        raise ValueError(
                            "Cache contains a nonregular or hard-linked file"
                        )
                artifacts = local_path(folder, "artifacts")
                artifacts.mkdir(exist_ok=True)
                target = local_path(folder, "config.toml")
                # Generate our own options; never load a shipped or user-edited TOML as policy.
                text = (
                    "[cache]\ndirectory = "
                    + json.dumps(artifacts.as_posix(), ensure_ascii=False)
                    + '\nfiles-total-size-soft-limit = "128Mi"\n'
                    + 'file-count-soft-limit = "4096"\n'
                )
                descriptor, name = tempfile.mkstemp(dir=folder)
                temporary = Path(name)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                        output.write(text)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
                # The native API changes the Config only after parsing/validating succeeds.
                config.cache = str(target)
            return {"state": "enabled", "directory": str(artifacts)}
        except (
            OSError,
            ValueError,
            RuntimeError,
            AttributeError,
            WasmtimeError,
        ) as error:
            return {"state": "disabled", "detail": str(error)[:512]}
