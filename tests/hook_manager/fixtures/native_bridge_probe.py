# SPDX-License-Identifier: MPL-2.0
"""Run the real bridge against an owned target; copied beside entry.py before execution."""

import importlib.util, json, runpy, sys, time
import io
import logging
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parent.parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, root / "launcher" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frida = load("test_binding", "native_test_binding.py").load(root)
load("dependencies", "bundled_dependencies.py").load_source_packages(root)
from launcher import process_lifecycle as lifecycle, game_bridge as bridge
output = Path(sys.argv[3])
messages = io.StringIO()
logger = logging.getLogger("owned.bridge_probe")
logger.setLevel(logging.INFO)
logger.propagate = False
handler = logging.StreamHandler(messages)
handler.setFormatter(logging.Formatter("%(levelno)s %(message)s"))
logger.addHandler(handler)
# The bridge receives the installation folder beside this isolated launcher copy.
pack = root.parent / "data/owned pack"
pack.mkdir(parents=True)
controller = lifecycle.Controller(
    frida.get_local_device(),
    [sys.argv[1], "-B", sys.argv[2], str(output)],
    logger,
    bridge_factory=bridge.factory(root, frida),
    restart_grace=0.2,
)
try:
    controller.start()
    deadline = time.monotonic() + 12
    while not (output / "active.json").exists() and time.monotonic() < deadline:
        controller.poll(timeout=0.02)
    controller.poll(timeout=0.02)
    assert (output / "active.json").exists(), messages
    assert any("HOOK MANAGER READY" in message for message in messages.getvalue().splitlines()), messages
    current = next(iter(controller.bridges.values()))
    # The helper waits for a worker that crosses another SaveThread hook.
    # This must finish without locking that worker out of JavaScript with the GIL.
    assert current.set_loading(dict(state="loading", loaded=0, failed=0, total=31))["menu"]["loading"]["total"] == 31
    current.set_loading(dict(state="loading", loaded=0, failed=0, total=1))
    status = current.set_menu(
        [
            {
                "owner": "owned.ui",
                "id": "proof",
                "label": "Owned UI",
                "message": "Sandbox UI capability",
            }
        ]
    )
    assert status["menu"]["state"] == "ready", status
    assert status["menu"]["loading"]["blocked"], status
    status = current.set_loading(dict(state="error", loaded=0, failed=1, total=1))
    deadline = time.monotonic() + 5
    while not (output / "themes-error-cleaned").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (output / "themes-error-cleaned").exists(), messages
    status = current.set_loading(dict(state="complete", loaded=1, failed=0, total=1))
    assert not status["menu"]["loading"]["blocked"], status
    deadline = time.monotonic() + 5
    while not (output / "menu.json").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (output / "menu.json").exists(), messages
    settings = [{"owner": "owned.settings", "label": "Owned Settings", "schema": 1, "fields": [
        {"id": "accent", "label": "Accent", "type": "color", "default": "#112233", "value": "#112233"},
    ]}]
    status = current.set_settings(settings)
    assert status["settings"]["state"] == "ready", status
    deadline = time.monotonic() + 5
    while status["settings"]["save"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
        status = current.request("status")
    request = status["settings"]["save"]
    assert request == {"id": 1, "owner": "owned.settings", "schema": 1, "values": {"accent": "#ABCDEF"}}, status
    settings[0]["fields"][0]["value"] = "#ABCDEF"
    status = current.set_settings(settings, {"id": 1, "owner": "owned.settings", "success": True})
    assert status["settings"]["save"] is None
    while not (output / "settings-acknowledged").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (output / "settings-acknowledged").exists(), messages
    colours = {role: "#" + "".join(f"{channel:02X}" for channel in colour)
               for _, colour, roles in runpy.run_path(str(Path(sys.argv[2]).with_name("palette.py")))["COLOR_GROUPS"]
               for role in roles}
    colours["BACKGROUND"] = "#ABCDEF"
    settings_generation = status["settings"]["generation"]
    status = current.set_themes([{"owner": "owned.theme", "label": "Custom Theme", "colours": colours}])
    assert status["themes"]["state"] == "ready", status
    assert status["settings"]["generation"] == settings_generation
    deadline = time.monotonic() + 5
    while not (output / "themes-applied.json").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    applied = json.loads((output / "themes-applied.json").read_text())
    assert applied == {"background": [171, 205, 239], "choices": ["blue", "emerald", "code-mod:owned.theme"]}
    (output / "settings-again-request").touch()
    while status["settings"]["save"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
        status = current.request("status")
    assert status["settings"]["save"] == {
        "id": 2, "owner": "owned.settings", "schema": 1, "values": {"accent": "#123456"},
    }, status
    assert status["settings"]["generation"] == settings_generation
    settings[0]["fields"][0]["value"] = "#123456"
    status = current.set_settings(settings, {"id": 2, "owner": "owned.settings", "success": True})
    while not (output / "settings-again-acknowledged").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert (output / "settings-again-acknowledged").exists(), messages
    attempts = []
    pid = next(iter(controller.sessions))
    controller.pack_mods[pid] = SimpleNamespace(career_preload=attempts.append, poll=lambda: None, close=lambda: None,
        menu_status=lambda status: None, settings_status=lambda status: None, themes_status=lambda status: None,
        readiness_status=lambda status: None)
    current.request("await-regulations")
    deadline = time.monotonic() + 3
    while not any(row["state"] == "waiting" for row in attempts) and time.monotonic() < deadline:
        controller.poll(timeout=.02)
    waiting = [row for row in attempts if row["state"] == "waiting"]
    assert len(waiting) == 1, attempts
    for _ in range(3):
        current.request("status")
        controller.poll()
    assert sum(row["state"] == "waiting" for row in attempts) == 1, attempts
    (output / "leave-request").touch()
    deadline = time.monotonic() + 3
    while not any(row["state"] == "left" for row in attempts) and time.monotonic() < deadline:
        controller.poll(timeout=.02)
    assert any(row["state"] == "left" and row["generation"] == waiting[0]["generation"] for row in attempts), attempts
    assert current.request("retire-regulations", regulations={"generation": waiting[0]["generation"],
        "endpoint": {"port": 1, "token": "0" * 64}}, _service=lambda: None)["regulation_control"] is True
    assert current.request("stop-regulations", regulations={"generation": waiting[0]["generation"],
        "endpoint": {"port": 1, "token": "0" * 64}})["regulation_control"] is True
    (output / "pause-request").touch()
    deadline = time.monotonic() + 5
    while not (output / "pause-ready").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert (output / "pause-ready").exists(), "Helper did not accept the pause"
    # A first request may finish at the last main-thread entry before the blocking wait.
    # Retry until a request times out; the helper cannot resume until we release it.
    while True:
        try:
            current.request("status", timeout=0.02)
        except bridge.BridgeError as error:
            assert "Timed out" in str(error), error
            break
        assert time.monotonic() < deadline, "Helper never blocked main-thread requests"
    (output / "release-request").touch()
    # A cancelled wait must not leave a queued request blocking this one or shutdown.
    status = current.request("status")
    assert status["groups"] == {"owned": "active"}, status
    assert status["pack"] == {
        "state": "ready",
        "series": "formula",
        "id": "owned pack",
        "root": str(pack.resolve()),
    }, status
    controller.poll()
    assert sum("PACK DISCOVERY" in message for message in messages.getvalue().splitlines()) == 1, messages
    errors = [line for line in messages.getvalue().splitlines() if line.startswith("40 ")]
    assert len(errors) == 1 and errors[0].endswith("Owned bridge validation failure"), messages.getvalue()
    try:
        current.request("execute")
        raise AssertionError("Unexpected arbitrary operation")
    except bridge.BridgeError:
        pass
finally:
    # Also release on assertion failure so cleanup can reach the helper's main thread.
    (output / "release-request").touch()
    controller.close()
assert not controller.cleanup_failed, messages
deadline = time.monotonic() + 5
while not (output / "closed.json").exists() and time.monotonic() < deadline:
    time.sleep(0.02)
assert (output / "closed.json").exists(), messages
logger.removeHandler(handler)
handler.close()
print(json.dumps(messages.getvalue().splitlines()))
