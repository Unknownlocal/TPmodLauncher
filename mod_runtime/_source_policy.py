# SPDX-License-Identifier: MPL-2.0
"""Portable source path rules and limits shared by the host and frozen guest.

Runtime module-name admission belongs to the guest's actual Python environment.
"""

import keyword
from pathlib import PurePosixPath

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCE_FILES = 256
_DEVICES = {"con", "prn", "aux", "nul"} | {f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)}


def module_part(name):
    if not name.isascii() or not name.isidentifier() or keyword.iskeyword(name) or name.casefold() in _DEVICES:
        raise ValueError("Expected a portable Python module identifier")


def source_path(path):
    item = PurePosixPath(path)
    if (not path or len(path) > 256 or item.is_absolute()
        or str(item) != path or "\\" in path or ".." in item.parts or item.suffix != ".py"):
        raise ValueError("Expected a canonical relative Python source path")
    for part in (*item.parts[:-1], item.stem):
        module_part(part)
    return path
