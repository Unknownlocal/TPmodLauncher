# SPDX-License-Identifier: MPL-2.0
"""Read and atomically replace settings.json relative to a pinned directory handle."""

import os
import re
import stat
import uuid

from mod_runtime import _source_files as disk

SETTINGS_FILE = "settings.json"


class _WindowsFiles:
    """Use native directory-relative operations without resolving ancestor paths again.

    NtCreateFile's RootDirectory and FILE_OPEN_REPARSE_POINT keep final opens
    anchored. FileRenameInformation uses that same handle for atomic replacement.
    https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile
    https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information
    """

    def __init__(self, filename=SETTINGS_FILE):
        import ctypes as c
        from ctypes import wintypes as w
        import msvcrt

        self.c, self.w, self.msvcrt = c, w, msvcrt
        self.filename = filename

        class UnicodeString(c.Structure):
            _fields_ = [("length", w.USHORT), ("maximum", w.USHORT), ("buffer", w.LPWSTR)]

        class ObjectAttributes(c.Structure):
            _fields_ = [("length", w.ULONG), ("root", w.HANDLE),
                        ("name", c.POINTER(UnicodeString)), ("attributes", w.ULONG),
                        ("security", c.c_void_p), ("quality", c.c_void_p)]

        class IoStatus(c.Structure):
            _fields_ = [("status", c.c_void_p), ("information", c.c_size_t)]

        self.UnicodeString, self.ObjectAttributes, self.IoStatus = UnicodeString, ObjectAttributes, IoStatus
        native = c.WinDLL("ntdll")
        self.create = native.NtCreateFile
        self.create.argtypes = (c.POINTER(w.HANDLE), w.DWORD, c.POINTER(ObjectAttributes),
                                c.POINTER(IoStatus), c.c_void_p, w.ULONG, w.ULONG,
                                w.ULONG, w.ULONG, c.c_void_p, w.ULONG)
        self.create.restype = w.LONG
        self.set_information = native.NtSetInformationFile
        self.set_information.argtypes = (w.HANDLE, c.POINTER(IoStatus), c.c_void_p, w.ULONG, w.ULONG)
        self.set_information.restype = w.LONG
        self.error_code = native.RtlNtStatusToDosError
        self.error_code.argtypes = (w.LONG,)
        self.error_code.restype = w.ULONG
        self.close = c.WinDLL("kernel32").CloseHandle
        self.close.argtypes = (w.HANDLE,)
        self.close.restype = w.BOOL

    def _check(self, status):
        if status < 0:
            raise self.c.WinError(self.error_code(status))

    def open(self, parent, name, *, create=False):
        c, w = self.c, self.w
        text = c.create_unicode_buffer(name)
        length = len(name.encode("utf-16-le"))
        string = self.UnicodeString(length, length + 2, c.cast(text, w.LPWSTR))
        attributes = self.ObjectAttributes(c.sizeof(self.ObjectAttributes),
                                          self.msvcrt.get_osfhandle(parent), c.pointer(string), 0x40, None, None)
        handle, status = w.HANDLE(), self.IoStatus()
        # GENERIC_WRITE | DELETE | SYNCHRONIZE for the exclusive temporary file;
        # GENERIC_READ | SYNCHRONIZE and read sharing for an existing settings file.
        access = 0x40110000 if create else 0x80100000
        # FILE_CREATE / FILE_OPEN; NON_DIRECTORY | SYNCHRONOUS_IO_NONALERT |
        # OPEN_REPARSE_POINT. Names are fixed host-owned leaves, never guest paths.
        self._check(self.create(c.byref(handle), access, c.byref(attributes), c.byref(status),
                                None, 0x80, 0 if create else 1, 2 if create else 1,
                                0x00200060, None, 0))
        try:
            return self.msvcrt.open_osfhandle(handle.value, os.O_BINARY | (os.O_WRONLY if create else os.O_RDONLY))
        except BaseException:
            self.close(handle)
            raise

    def rename(self, descriptor, parent):
        c, w = self.c, self.w

        class Rename(c.Structure):
            _fields_ = [("replace", w.BOOLEAN), ("root", w.HANDLE),
                        ("length", w.ULONG), ("name", w.WCHAR * len(self.filename))]

        data = Rename(True, self.msvcrt.get_osfhandle(parent), len(self.filename) * 2, self.filename)
        status = self.IoStatus()
        self._check(self.set_information(self.msvcrt.get_osfhandle(descriptor), c.byref(status),
                                        c.byref(data), c.sizeof(data), 10))

    def discard(self, descriptor):
        data, status = self.w.BOOLEAN(True), self.IoStatus()
        self._check(self.set_information(self.msvcrt.get_osfhandle(descriptor), self.c.byref(status),
                                        self.c.byref(data), self.c.sizeof(data), 13))


def _filename(value):
    # Callers choose a fixed host-owned leaf (settings.json or catalogue.json),
    # never a mod-supplied path. ASCII also keeps Windows UTF-16 lengths simple.
    if type(value) is not str or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", value):
        raise ValueError("Expected a short host-owned file name")
    return value


def read(root, parent, maximum, *, filename=SETTINGS_FILE):
    filename = _filename(filename)
    if os.name != "nt":
        return disk.read_file(root / filename, parent, maximum)
    native = _WindowsFiles(filename)
    with os.fdopen(native.open(parent, filename), "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Expected an ordinary, unshared settings file without reparse points")
        if info.st_size > maximum:
            raise ValueError("Settings file exceeds its byte limit")
        data = stream.read(maximum + 1)
        if len(data) > maximum or len(data) != info.st_size:
            raise ValueError("Settings file changed or exceeds its byte limit")
        return data


def write(parent, data, *, filename=SETTINGS_FILE):
    """Publish a complete UTF-8 document, retaining the previous file on failure."""
    filename = _filename(filename)
    # Keep the legacy temporary prefix for both consumers. The destination is
    # replaced relative to the pinned parent, after the complete document flushes.
    temporary = ".settings-" + uuid.uuid4().hex + ".tmp"
    if os.name == "nt":
        native = _WindowsFiles(filename)
        with os.fdopen(native.open(parent, temporary, create=True), "wb") as stream:
            try:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                native.rename(stream.fileno(), parent)
            except BaseException:
                native.discard(stream.fileno())
                raise
    else:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, filename, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
