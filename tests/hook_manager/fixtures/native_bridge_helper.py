# SPDX-License-Identifier: MPL-2.0
"""Owned Python target for hook activation, timeout coordination and restoration."""

import importlib, json, sys, time

# No threading module exists yet: the bridge must wait rather than choose the wrong main thread.
time.sleep(0.1)
assert "_tpm_hook_manager_host" not in sys.modules
import threading
from pathlib import Path
from types import ModuleType

output = Path(sys.argv[1])
deadline = time.monotonic() + 15
while "_tpm_hook_manager_host" not in sys.modules and time.monotonic() < deadline:
    time.sleep(0.02)
host = sys.modules["_tpm_hook_manager_host"]
manager = host.manager


# Finish an owned Formula-like startup after the bridge has already become ready.
# The helper raises if discovery calls it instead of reading the populated cache.
def active_data_pack(default="owned-default"):
    raise AssertionError("Discovery called the selection helper")


utils = ModuleType("utils")
utils.active_data_pack = active_data_pack
utils._ACTIVE_PACK_CACHE = {"owned-default": "owned pack"}
sys.modules["utils"] = utils
_CONSTANTS_LOADED = True
while host.discovery.snapshot is None and time.monotonic() < deadline:
    time.sleep(0.02)
assert host.discovery.snapshot is not None, "Bridge did not discover the startup pack"
# Simulate a menu selection while this process still owns the original loaded pack.
utils._ACTIVE_PACK_CACHE.clear()


# Normal import exercises the readiness observer before this first menu consumer.
sys.path.insert(0, str(Path(__file__).parent))
hub = importlib.import_module("ui.screens.hub_screen")
HubScreen = hub.HubScreen
original_menu = hub.ORIGINAL_METHODS["_activate"]
original_draw = hub.ORIGINAL_METHODS["draw"]
original_enabled = hub.ORIGINAL_METHODS["_item_enabled"]
original_disabled_message = hub.ORIGINAL_METHODS["_disabled_item_message"]
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
# The startup observer must have armed the guard before a normal menu consumer runs.
screen = HubScreen()
assert all(
    not screen._item_enabled(item)
    for item in ("New Season", "Continue Season", "Load Game")
)
assert screen._activate("New Season") is None
assert (
    screen._item_enabled("Options") and screen._activate("Options") == "native Options"
)
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
    if (
        "Owned UI" in hub.MENU_ITEMS
        and host.menu.loading["state"] == "complete"
        and not (output / "menu.json").exists()
    ):
        screen = HubScreen()
        assert screen._item_enabled("New Season") and screen._item_enabled("Load Game")
        assert not screen._item_enabled("Continue Season")
        screen._activate("Owned UI")
        (output / "menu.json").write_text(
            json.dumps(
                {
                    "items": hub.MENU_ITEMS,
                    "message": screen.error_message,
                    "until": screen.error_until,
                    "native": screen._activate("Exit"),
                }
            )
        )
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
            "pack": host.discovery.poll(),
            "menu": hub.MENU_ITEMS,
            "menu_restored": HubScreen._activate is original_menu
            and HubScreen.draw is original_draw
            and HubScreen._item_enabled is original_enabled,
            "disabled_message_restored": HubScreen._disabled_item_message
            is original_disabled_message,
        }
    )
)
