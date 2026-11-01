# SPDX-License-Identifier: MPL-2.0
"""Owned Python target for hook activation, timeout coordination and restoration."""

import importlib, json, os, sys, time
from pathlib import Path
from types import ModuleType

# No threading module exists yet: the bridge must wait rather than choose the wrong main thread.
time.sleep(0.1)
assert "_tpm_hook_manager_host" not in sys.modules
import threading

output = Path(sys.argv[1])
(output / "target.pid").write_text(str(os.getpid()), encoding="ascii")
deadline = time.monotonic() + 15
while "_tpm_hook_manager_host" not in sys.modules and time.monotonic() < deadline:
    time.sleep(0.02)
host = sys.modules["_tpm_hook_manager_host"]
manager = host.manager
host.adapters.enqueue_log_record({"level": "ERROR", "detail": "Owned bridge validation failure"})

# Reproduce the bridge lock ordering with real Python threads. Evaluating the
# loading request releases the GIL; the worker must be able to pass its native
# SaveThread hook, release the GIL itself, and finish that request's wait.
loading_start = threading.Event()

def loading_worker():
    loading_start.wait()
    time.sleep(.001)

loading_thread = threading.Thread(target=loading_worker, daemon=True)
loading_thread.start()
original_loading = host.menu.set_loading

def set_loading(record):
    if record["total"] == 31:
        loading_start.set()
        loading_thread.join(timeout=2)
        assert not loading_thread.is_alive(), "Bridge blocked a worker holding the GIL"
    if record["total"] == 32:
        (output / "evaluation-ready").touch()
        time.sleep(.2)  # Allow the client to cancel while Python has released the GIL.
    return original_loading(record)

host.menu.set_loading = set_loading

original_control = host.adapters.control_regulations
def control_regulations(*args, **kwargs):
    result = original_control(*args, **kwargs)
    if result is False and (output / "early-ready").exists() and not (output / "early-completed").exists():
        # Force completion to reach JavaScript before this WAIT reply. The
        # admitted worker is real; only the reply is deliberately delayed.
        (output / "early-release").touch()
        early_thread.join(2)
        assert not early_thread.is_alive()
        time.sleep(.1)
        (output / "early-completed").touch()
    return result
host.adapters.control_regulations = control_regulations


# Finish an owned Formula-like startup after the bridge has already become ready.
# The helper raises if discovery calls it instead of reading the populated cache.
def active_data_pack(default="owned-default"):
    raise AssertionError("Discovery called the selection helper")


utils = ModuleType("utils")
utils.active_data_pack = active_data_pack
utils._ACTIVE_PACK_CACHE = {"owned-default": "owned pack"}
sys.modules["utils"] = utils
_CONSTANTS_LOADED = True
# The real entry script imports the hub after setting this marker. Its import
# completion, rather than a later status pulse, discovers the fixed startup pack.
sys.path.insert(0, str(Path(__file__).parent))
hub = importlib.import_module("ui.screens.hub_screen")
HubScreen = hub.HubScreen
while host.discovery.snapshot is None and time.monotonic() < deadline:
    time.sleep(0.02)
assert host.discovery.snapshot is not None, "Bridge did not discover the startup pack"
# Simulate a menu selection while this process still owns the original loaded pack.
utils._ACTIVE_PACK_CACHE.clear()


# Normal import exercises the readiness observer before this first menu consumer.
# A disposable loading job lets the client check status-change delivery through
# the real JavaScript bridge. It performs no actual game or asset work.
preload_module = ModuleType("ui.session_preload")
def advance_session_preload(screen, state):
    return True
preload_module.advance_session_preload = hub.advance_session_preload = advance_session_preload
sys.modules[preload_module.__name__] = preload_module
game = ModuleType("game_state")
class GameState:
    def advance_week(self, off_week=False, *, persist=True):
        return True
    def save_season(self, *, manual=False):
        if getattr(self, "fail_save", False):
            raise OSError("Owned native save failed " + "x" * 5000 + " FULL SAVE END")
        return "owned save"
    def finalize_and_rollover_season(self):
        return None
game.GameState = GameState
sys.modules[game.__name__] = game
loading_screen = HubScreen()
navigation = ModuleType("ui.screen_manager")
navigation.ScreenManager = type("OwnedManager", (), {"switch_to": lambda self, screen: setattr(self, "current", screen)})
sys.modules[navigation.__name__] = navigation
loading_screen.manager = navigation.ScreenManager()
loading_screen.manager.current = loading_screen
loading_screen.state = game.GameState()
loading_screen._preload_job = type("OwnedJob", (), {})()
options = importlib.import_module("ui.screens.options_screen")
from native_themes_fixture import modules
theme_path = output / "display_settings.json"
theme_path.write_text('{"color_pack":"code-mod:owned.theme"}')
theme, packs = modules(Path(__file__).with_name("palette.py"), theme_path)
sys.modules[theme.__name__], sys.modules[packs.__name__] = theme, packs
options.COLOR_PACKS = packs.COLOR_PACKS
host.themes.poll()  # Native post-import arm precedes background mod registration.
host.events.imported(host.manager)  # Model completed imports for the synthetic modules above.
assert host.themes.placeholder is not None
original_palette_getitem = packs.ChromePalette.__getitem__
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
    if (output / "early-request").exists() and not (output / "early-ready").exists():
        early_guards = host.CallableAdapters()
        host.adapters.regulations.append(early_guards)
        def early_work():
            with early_guards.operation():
                (output / "early-ready").touch()
                limit = time.monotonic() + 8
                while not (output / "early-release").exists() and time.monotonic() < limit:
                    time.sleep(.01)
        early_thread = threading.Thread(target=early_work, daemon=True)
        early_thread.start()
        while not (output / "early-ready").exists():
            time.sleep(.001)
    if (output / "import-request").exists() and "constants" not in sys.modules:
        threading.Thread(target=lambda: importlib.import_module("constants"), daemon=True).start()
    if (output / "busy-request").exists() and not (output / "busy-ready").exists():
        guards = host.CallableAdapters()
        host.adapters.regulations.append(guards)
        def admitted_work():
            with guards.operation():
                (output / "busy-ready").touch()
                limit = time.monotonic() + 8
                while not (output / "busy-release").exists() and time.monotonic() < limit:
                    time.sleep(.01)
        threading.Thread(target=admitted_work, daemon=True).start()
        # Do not admit another worker while this one reaches its operation scope.
        while not (output / "busy-ready").exists():
            time.sleep(.001)
    if ((output / "activity-request").exists() and not (output / "activity-done").exists()
            and host.activity.group_intact("activity.save")):
        loading_screen.state.fail_save = True
        journal = host.activity.journal
        host.activity.journal = host.ActivityJournal(output / "missing-native-journal.jsonl")
        try:
            loading_screen.state.save_season(manual=True)
        except OSError as error:
            assert error.launcher_activity["kind"] == "saving"
            (output / "activity-done").touch()
        finally:
            host.activity.journal = journal
    if (output / "leave-request").exists() and loading_screen.manager.current is loading_screen:
        loading_screen.manager.switch_to(HubScreen())
    if host.adapters.preload is not None and loading_screen.manager.current is loading_screen:
        assert hub.advance_session_preload(loading_screen, loading_screen.state) is False
    if (host.menu.loading or {}).get("state") == "error" and host.themes.placeholder is None:
        assert "code-mod:owned.theme" not in packs.COLOR_PACK_LABELS
        (output / "themes-error-cleaned").touch()
    if host.themes.applied == "owned.theme" and not (output / "themes-applied.json").exists():
        theme_proof = output / "themes-applied.tmp"
        theme_proof.write_text(json.dumps({
            "background": theme.UITheme.BACKGROUND, "choices": options.COLOR_PACKS,
        }))
        theme_proof.replace(output / "themes-applied.json")
    if host.settings.records and not (output / "settings-requested").exists():
        host.settings.queue_save("owned.settings", {"schema": 1, "values": {"accent": "#ABCDEF"}})
        (output / "settings-requested").touch()
    if (host.settings.records and host.settings.pending is None
            and host.settings.records[0]["fields"][0]["value"] == "#ABCDEF"):
        (output / "settings-acknowledged").touch()
    if (output / "settings-again-request").exists() and not (output / "settings-again-requested").exists():
        host.settings.queue_save("owned.settings", {"schema": 1, "values": {"accent": "#123456"}})
        (output / "settings-again-requested").touch()
    if (host.settings.records and host.settings.pending is None
            and host.settings.records[0]["fields"][0]["value"] == "#123456"):
        (output / "settings-again-acknowledged").touch()
    if (
        "Owned UI" in hub.MENU_ITEMS
        and (host.menu.loading or {}).get("state") == "complete"
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
            "settings_tabs": options.OPTION_TABS,
            "settings_restored": all(getattr(options.OptionsScreen, name) is original
                                     for name, original in options.ORIGINAL_METHODS.items()),
            "theme_background": theme.UITheme.BACKGROUND,
            "theme_choices": options.COLOR_PACKS,
            "theme_restored": packs.ChromePalette.__getitem__ is original_palette_getitem,
        }
    )
)
