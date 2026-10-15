# SPDX-License-Identifier: MPL-2.0
"""Collect elapsed seconds for trusted mod-loading phases."""

from contextlib import contextmanager
from time import perf_counter


@contextmanager
def measure(timings, phase):
    """Store a phase's duration in the host's dictionary, even when that phase fails.

    timings may be None when the caller does not collect loading measurements.
    phase is a fixed host label. No guest callback runs when a measurement ends.
    These are elapsed seconds, separate from the guest's fuel and time limits.
    """
    if timings is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        timings[phase] = round(perf_counter() - started, 6)
