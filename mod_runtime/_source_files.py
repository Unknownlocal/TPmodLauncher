# SPDX-License-Identifier: MPL-2.0
"""Read source through pinned ordinary directories; never follow author links."""

from contextlib import contextmanager
import os
from pathlib import Path
import stat


def _windows_open(path, directory, writable_directory=False):
    # By default, no write/delete sharing: opened files and ancestor directories
    # cannot be replaced or converted into reparse points during a source read.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    # FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES makes directory sharing locks
    # effective too; metadata-only handles do not prevent renaming on Windows.
    # FILE_SHARE_READ, OPEN_EXISTING, OPEN_REPARSE_POINT | BACKUP_SEMANTICS.
    # UI settings storage permits writes inside its final pinned directory. Ancestors
    # and source snapshots keep the original read-only sharing policy.
    share = 3 if directory and writable_directory else 1
    handle = create(str(path), 0x81 if directory else 0x80000000, share, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        error.filename = str(path)
        raise error
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise


@contextmanager
def opened(path, parent=None, *, directory=False, writable_directory=False):
    """Keep ownership of a no-follow descriptor until its caller finishes."""
    if os.name == "nt":
        descriptor = _windows_open(path, directory, writable_directory)
    else:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        if directory:
            flags |= os.O_DIRECTORY
        descriptor = os.open(path.name if parent is not None else path, flags, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Source paths must not be linked or reparse points")
        if directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Expected an ordinary source directory")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Expected an ordinary, unshared source file")
        yield descriptor
    finally:
        os.close(descriptor)


def root_directory(stack, folder, *, writable=False):
    """Pin every ancestor; writable permits Windows writes in the final directory.

    Writers must use directory-relative operations against the returned handle.
    Source snapshots retain the stricter default sharing policy.
    """
    path = Path(folder).absolute()
    if ".." in path.parts:
        raise ValueError("Expected a canonical mod folder")
    parent = None
    current = Path(path.anchor)
    for part in (path.anchor, *path.parts[1:]):
        if part != path.anchor:
            current /= part
        parent = stack.enter_context(opened(current, parent, directory=True,
                                           writable_directory=writable and current == path))
    return path, parent


def stamp(info):
    """Ignore read access times when checking for observed replacement or mutation."""
    # Windows path stat and descriptor fstat expose different ctime meanings on
    # supported Python versions. Write/delete sharing is denied there instead.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, 0 if os.name == "nt" else info.st_ctime_ns)


def read_file(path, parent, maximum, expected=None):
    """Bound the read and reject files replaced or modified while being read."""
    with opened(path, parent) as descriptor:
        before = os.fstat(descriptor)
        if expected is not None and stamp(before) != stamp(expected):
            raise ValueError("Source file changed before reading")
        if before.st_size > maximum:
            raise ValueError("Source input exceeds its byte limit")
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise ValueError("Source input exceeds its byte limit")
        if size != before.st_size or stamp(before) != stamp(os.fstat(descriptor)):
            raise ValueError("Source file changed during reading")
        return b"".join(chunks)
