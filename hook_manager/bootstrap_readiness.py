# SPDX-License-Identifier: MPL-2.0
"""Check existing interpreter state before parsing the full bootstrap bundle."""


def bootstrap_readiness():
    """Return a wait reason or READY without importing any game or worker module."""
    import sys

    threads = sys.modules.get("threading")
    if threads is None or getattr(getattr(threads, "__spec__", None), "_initializing", False):
        return "WAIT: threading is not ready"
    if threads.current_thread() is not threads.main_thread():
        return "WAIT: other thread"
    # Interrupting one of these imports could deadlock the reporting workers.
    for dependency in ("socket", "_socket", "select", "selectors", "struct", "_struct", "copy"):
        if getattr(getattr(sys.modules.get(dependency), "__spec__", None), "_initializing", False):
            return "WAIT: reporting dependency is importing: " + dependency
    return "READY"
