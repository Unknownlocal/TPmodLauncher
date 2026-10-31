# SPDX-License-Identifier: MPL-2.0
"""One bounded UTF-8 transfer; partial chunks are never parsed as JSON."""

from .values import integer


class RegulationTransfer:
    def __init__(self, kind, length):
        self.kind, self.remaining = kind, integer(length, positive=True)
        self.parts = []

    def put(self, chunk):
        if type(chunk) is not str:
            raise ValueError("Expected a resident UTF-8 chunk")
        size = len(chunk.encode("utf-8"))
        # 4 KiB bounds each call, not the whole transfer. The declared remaining
        # byte count permits larger documents while rejecting overflow/empty puts.
        if not 1 <= size <= min(4096, self.remaining):
            raise ValueError("Resident chunk exceeds transfer bounds")
        self.remaining -= size
        self.parts.append(chunk)

    def finish(self, kind):
        # A transfer cannot be reused as another message kind or parsed early.
        if kind != self.kind or self.remaining:
            raise ValueError("Wrong or incomplete resident transfer")
        value = "".join(self.parts)
        self.parts = []
        return value
