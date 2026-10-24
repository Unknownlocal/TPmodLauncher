# SPDX-License-Identifier: MPL-2.0
"""Process working-set observations and scoped Python object-graph sizes."""

import ctypes
import sys


class MemoryCounters(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("faults", ctypes.c_uint32),
                *[(name, ctypes.c_size_t) for name in ("peak", "working", "pool_peak", "pool", "nonpool_peak", "nonpool", "pagefile", "pagefile_peak", "private")]]


def process_memory():
    if sys.platform == "win32":
        from ctypes import wintypes
        value = MemoryCounters()
        value.cb = ctypes.sizeof(value)
        kernel, api = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD)
        if not api.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"working_bytes": value.working, "process_peak_bytes": value.peak, "private_bytes": value.private}
    if sys.platform.startswith("linux"):
        from pathlib import Path
        fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines())
        return {"working_bytes": int(fields["VmRSS"].split()[0]) * 1024,
                "process_peak_bytes": int(fields["VmHWM"].split()[0]) * 1024}
    import resource
    return {"process_peak_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def retained_size(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size, kind = sys.getsizeof(value), type(value)
    if kind is dict:
        size += sum(retained_size(key, seen) + retained_size(child, seen) for key, child in value.items())
    elif kind in (list, tuple, set, frozenset):
        size += sum(retained_size(child, seen) for child in value)
    elif kind.__module__.startswith("launcher.regulations"):
        if hasattr(value, "__dict__"):
            size += retained_size(vars(value), seen)
        else:
            size += sum(retained_size(getattr(value, key), seen) for key in getattr(kind, "__slots__", ()))
    return size
