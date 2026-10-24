# SPDX-License-Identifier: MPL-2.0
"""Keep a group's source copies and runner code until the caller uses them."""

from dataclasses import dataclass

from launcher.compiled_mod import CompiledMod
from .source import SourceSnapshot


@dataclass
class PreparedSources:
    """Hold an ordered group before creating its shared Python interpreter.

    Before polling, a cancelled read can dispose its result on the worker. Once
    take() requests compilation, the controller owns closure. Closing drops the
    source copies and runner bytes; a running interpreter keeps its code reference.
    """

    snapshots: tuple[SourceSnapshot, ...] | None
    component: bytes | None
    timings: dict
    compiled: CompiledMod | None = None

    def close(self):
        """Release group inputs and this request, leaving other code users intact."""
        self.snapshots = None
        self.component = None
        if self.compiled is not None:
            self.compiled.close()

    def __enter__(self):
        if self.snapshots is None:
            raise RuntimeError("Source group preparation result is closed")
        return self

    def __exit__(self, *_):
        self.close()
