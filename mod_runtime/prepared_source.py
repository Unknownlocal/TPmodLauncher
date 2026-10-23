# SPDX-License-Identifier: MPL-2.0
"""Keep inert source and its code request owned until admission or cancellation."""

from dataclasses import dataclass

from launcher.compiled_mod import CompiledMod
from .source import SourceSnapshot


@dataclass
class PreparedSource:
    """Hold read source/bytes, then the request that takes ownership of those bytes.

    Before controller polling there is no compiler request, so a late reader
    result can be discarded directly on its worker. After polling, close runs
    on the controller and releases just this mod's request.
    """

    snapshot: SourceSnapshot | None
    component: bytes | None
    timings: dict
    compiled: CompiledMod | None = None

    def close(self):
        self.snapshot = None
        self.component = None
        if self.compiled is not None:
            self.compiled.close()

    def __enter__(self):
        if self.snapshot is None:
            raise RuntimeError("Source preparation result is closed")
        return self

    def __exit__(self, *_):
        self.close()
