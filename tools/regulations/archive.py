# SPDX-License-Identifier: MPL-2.0
"""Read the inspected game's Python archive without importing its modules.

This is developer tooling for the pinned executable, not a general archive reader
or a player runtime dependency. Check the complete file hash before unmarshalling;
the embedded code also requires the game's Python major/minor version.
"""

import hashlib
import marshal
from pathlib import Path
import struct
import sys
from types import CodeType
import zlib


def code_objects(code):
    yield code
    for value in code.co_consts:
        if type(value) is CodeType:
            yield from code_objects(value)


class GameArchive:
    def __init__(self, path, expected_sha256):
        raw = Path(path).read_bytes()
        self.sha256 = hashlib.sha256(raw).hexdigest()
        if self.sha256 != expected_sha256:
            raise ValueError("Game executable differs from the inspected project-mapping build")
        cookie = raw.rfind(b"MEI\x0c\x0b\x0a\x0b\x0e")
        _, size, offset, length, version, _ = struct.unpack("!8sIIII64s", raw[cookie:cookie + 88])
        if version != sys.version_info.major * 100 + sys.version_info.minor:
            raise ValueError(f"Use the game's Python version ({version}), not {sys.version_info[:2]}")
        base = cookie + 88 - size
        table, cursor = raw[base + offset:base + offset + length], 0
        while cursor < len(table):
            count, position, size, _, compressed, _ = struct.unpack("!iIIIBc", table[cursor:cursor + 18])
            name = table[cursor + 18:cursor + count].rstrip(b"\0").decode()
            if name == "PYZ.pyz":
                self.pyz = raw[base + position:base + position + size]
                if compressed:
                    self.pyz = zlib.decompress(self.pyz)
                self.index = dict(marshal.loads(self.pyz[struct.unpack("!i", self.pyz[8:12])[0]:]))
                break
            cursor += count
        else:
            raise ValueError("Game executable has no Python module archive")

    def module(self, name):
        _, position, size = self.index[name]
        return marshal.loads(zlib.decompress(self.pyz[position:position + size]))
