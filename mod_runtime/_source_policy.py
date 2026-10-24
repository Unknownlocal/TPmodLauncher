# SPDX-License-Identifier: MPL-2.0
"""Portable source path rules and limits shared by the host and frozen guest.

Runtime module-name admission belongs to the guest's actual Python environment.
"""

import keyword
import re
from pathlib import PurePosixPath

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCE_FILES = 256
# Bound a pack separately from each mod: adding owners must not multiply the
# group's retained source allowance without limit. These are admission ceilings,
# not a promise that every admitted program fits the runner's memory budget.
MAX_SOURCE_OWNERS = 128
MAX_GROUP_SOURCE_BYTES = 8 * 1024 * 1024
MAX_GROUP_SOURCE_FILES = 4096
SOURCE_NAMESPACE = "_mod_sources"
_DEVICES = {"con", "prn", "aux", "nul"} | {f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)}


def source_owner(owner):
    """Keep the manifest identity separate from its collision-free Python name."""
    if type(owner) is not str or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", owner):
        raise ValueError("Expected a bounded source mod ID")
    return SOURCE_NAMESPACE + ".m" + owner.encode("ascii").hex()


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
