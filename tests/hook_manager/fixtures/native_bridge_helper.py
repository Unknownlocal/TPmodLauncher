# SPDX-License-Identifier: MPL-2.0
"""Owned Python target for hook activation, timeout coordination and restoration."""

import importlib, json, sys, time

# No threading module exists yet: the bridge must wait rather than choose the wrong main thread.
time.sleep(0.1)
assert "_tpm_hook_manager_host" not in sys.modules
import threading
from pathlib import Path

output = Path(sys.argv[1])
deadline = time.monotonic() + 15
while "_tpm_hook_manager_host" not in sys.modules and time.monotonic() < deadline:
    time.sleep(0.02)
host = sys.modules["_tpm_hook_manager_host"]
manager = host.manager
manager.dispatch = lambda record: {"action": "replace", "value": 9}
manager.approve(
    host.Target(
        "owned.method", "owned_native_fixture", (), "method", "(value)", replace=True
    )
)
manager.install(
    "owned",
    [{"target": "owned.method", "callback": "owned.callback", "phase": "before"}],
)
sys.path.insert(0, str(Path(__file__).parent))
fixture = importlib.import_module("owned_native_fixture")
(output / "active.json").write_text(
    json.dumps({"value": fixture.method(3), "status": manager.status()})
)


def release_when_requested(signal):
    # Keep the main thread blocked until the client releases it; bound abandoned tests.
    limit = time.monotonic() + 10
    while not (output / "release-request").exists() and time.monotonic() < limit:
        time.sleep(0.01)
    signal.set()


paused = False
while not manager.closed and time.monotonic() < deadline:
    if (output / "pause-request").exists() and not paused:
        release = threading.Event()
        watcher = threading.Thread(
            target=release_when_requested, args=(release,), daemon=True
        )
        watcher.start()
        (output / "pause-ready").touch()
        # Only the watcher can release this wait; the client decides when to do that.
        assert release.wait(timeout=11), "Pause release watcher did not finish"
        assert (output / "release-request").exists(), "Client abandoned the pause"
        watcher.join(timeout=1)
        assert not watcher.is_alive(), "Pause release watcher stayed alive"
        paused = True
    time.sleep(0.02)
(output / "closed.json").write_text(
    json.dumps(
        {
            "value": fixture.method(3),
            "status": manager.status(),
            "observer_removed": manager.finder not in sys.meta_path,
        }
    )
)
