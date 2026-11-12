# SPDX-License-Identifier: MPL-2.0
"""Await an explicit launch's output before inspecting it in tests."""

from launcher.diagnostics import bind_reporter, sync_log


def read_log(path, sink=None):
    if sink is not None:
        owner = bind_reporter(sink).owner
        assert sync_log(sink), "Owned log did not synchronise"
        assert owner.output.console.checkpoint(1), "Owned console did not drain"
    return path.read_text(encoding="utf-8")
