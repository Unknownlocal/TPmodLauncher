# SPDX-License-Identifier: MPL-2.0
"""Inject our bridge into an owned Python process, exercising real GIL entry and restoration."""

from textwrap import dedent
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.embedded
def test_real_bridge_initialises_hooks_and_restores_before_detach(
    embedded_project, tmp_path
):
    root = embedded_project
    client = root / "libs/python-3.13.16-embed-amd64/python.exe"
    runtime = tmp_path / "owned Python target"
    shutil.copytree(client.parent, runtime)
    helper = tmp_path / "owned_helper.py"
    (tmp_path / "owned_native_fixture.py").write_text(
        "def method(value):\n    return value + 1\n"
    )
    helper.write_text(
        dedent(
            """\
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
            manager.approve(host.Target("owned.method", "owned_native_fixture", (), "method", "(value)", replace=True))
            manager.install("owned", [{"target": "owned.method", "callback": "owned.callback", "phase": "before"}])
            sys.path.insert(0, str(Path(__file__).parent))
            fixture = importlib.import_module("owned_native_fixture")
            (output / "active.json").write_text(json.dumps({"value": fixture.method(3), "status": manager.status()}))
            paused = False
            while not manager.closed and time.monotonic() < deadline:
                if (output / "pause-request").exists() and not paused:
                    (output / "sleeping").touch()
                    time.sleep(0.5)
                    paused = True
                time.sleep(0.02)
            (output / "closed.json").write_text(json.dumps({"value": fixture.method(3), "status": manager.status(),
                "observer_removed": manager.finder not in sys.meta_path}))
            """
        ),
        encoding="utf-8",
    )
    output = tmp_path / "results"
    output.mkdir()
    probe = root / "launcher/bridge_probe.py"
    probe.write_text(
        dedent(
            """\
            import importlib.util, json, sys, time
            from pathlib import Path
            root = Path(__file__).resolve().parent.parent
            def load(name, filename):
                spec = importlib.util.spec_from_file_location(name, root / "launcher" / filename)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            deps = load("deps", "bundled_dependencies.py")
            frida = deps.import_frida(root)
            lifecycle = load("lifecycle", "process_lifecycle.py")
            bridge = load("bridge", "game_bridge.py")
            output = Path(sys.argv[3])
            messages = []
            controller = lifecycle.Controller(frida.get_local_device(), [sys.argv[1], "-B", sys.argv[2], str(output)],
                messages.append, bridge_factory=bridge.factory(root, frida), restart_grace=0.2)
            try:
                controller.start()
                deadline = time.monotonic() + 12
                while not (output / "active.json").exists() and time.monotonic() < deadline:
                    controller.poll(timeout=0.02)
                controller.poll(timeout=0.02)
                assert (output / "active.json").exists(), messages
                assert any("HOOK MANAGER READY" in message for message in messages), messages
                current = next(iter(controller.bridges.values()))
                (output / "pause-request").touch()
                while not (output / "sleeping").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert (output / "sleeping").exists()
                time.sleep(0.05)
                try:
                    current.request("status", timeout=0.02)
                    raise AssertionError("Blocked target did not time out")
                except bridge.BridgeError as error:
                    assert "Timed out" in str(error), error
                # A cancelled wait must not leave a queued request blocking this one or shutdown.
                status = current.request("status")
                assert status["groups"] == {"owned": "active"}, status
                try:
                    current.request("execute")
                    raise AssertionError("Unexpected arbitrary operation")
                except bridge.BridgeError:
                    pass
            finally:
                controller.close()
            assert not controller.cleanup_failed, messages
            deadline = time.monotonic() + 5
            while not (output / "closed.json").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert (output / "closed.json").exists(), messages
            print(json.dumps(messages))
            """
        ),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [
                str(client),
                "-B",
                str(probe),
                str(runtime / "python.exe"),
                str(helper),
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        active = json.loads((output / "active.json").read_text())
        assert active["value"] == 9 and active["status"]["groups"] == {
            "owned": "active"
        }
        closed = json.loads((output / "closed.json").read_text())
        assert closed["value"] == 4 and closed["status"]["state"] == "stopped"
        assert (
            closed["status"]["groups"] == {"owned": "removed"}
            and closed["observer_removed"]
        )
        assert not closed["status"]["errors"]
    finally:
        probe.unlink(missing_ok=True)
