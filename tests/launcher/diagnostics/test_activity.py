# SPDX-License-Identifier: MPL-2.0
"""Observe activity on real scopes, log output and controller session replacement."""

from contextlib import closing
import json
import threading

import pytest

from launcher import process_lifecycle as lifecycle
from launcher.diagnostics import core as diagnostics
from tests.controller_device import rig


def test_nested_work_freezes_failure_and_does_not_leak_to_another_thread(tmp_path):
    outcome = {}
    path = tmp_path / "launcher.log"
    with diagnostics.reporter(path, outcome=outcome) as logger:
        tracker = logger.launch_activity
        with tracker.scope("game_loading") as outer:
            try:
                with tracker.scope("mod_loading", mod="owned.first") as inner:
                    other = []
                    worker = threading.Thread(target=lambda: other.append(tracker.snapshot()))
                    worker.start()
                    worker.join(2)
                    assert not worker.is_alive() and other[0]["kind"] == "launcher_startup"
                    assert inner["parent"] == outer["operation"]
                    raise RuntimeError("Owned activation failed")
            except RuntimeError as error:
                assert tracker.snapshot()["kind"] == "game_loading"
                with tracker.scope("shutdown"):
                    diagnostics.handle_error(logger, error)
    activity = outcome["failure"]["activity"]
    assert activity["mod"] == "owned.first" and activity["description"] == "while loading owned.first"
    assert "Owned activation failed" in path.read_text(encoding="utf-8")


def test_native_activity_rejects_old_career_and_replaced_process_events(rig, tmp_path):
    device, _, _, _ = rig
    outcome = {}
    path = tmp_path / "launcher.log"
    with diagnostics.reporter(path, outcome=outcome) as logger:
        controller = lifecycle.Controller(device, [device.game], logger)
        with closing(controller):
            controller.start()
            first = controller.sessions[10]
            def observe(session, kind, sequence, generation):
                controller.events.put(("bridge", (10, session, {"type": "send", "payload": {
                    "event": "readiness", "status": {"activity": {
                        "kind": kind, "sequence": sequence, "generation": generation}}}})))
                controller.poll()
            observe(first, "save_loading", 1, 0)
            diagnostics.write_log(logger, "Owned loading diagnostic", pid=10, level="ERROR")
            observe(first, "in_save", 3, 1)
            observe(first, "save_loading", 2, 0)
            observe(first, "save_loading", 4, 0)
            assert controller.activity.snapshot(10)["kind"] == "in_save"
            controller.events.put(("bridge", (10, first, {"type": "send", "payload": {
                "event": "diagnostic", "level": "ERROR", "detail": "Owned delayed loading diagnostic",
                "activity": {"kind": "save_loading", "sequence": 1, "generation": 0}}})))
            controller.poll()
            assert controller.activity.snapshot(10)["kind"] == "in_save"
            original_session = controller.activity.snapshot(10)["process_session"]
            first.is_detached = True
            device.child(10, device.game)
            controller.poll()
            observe(first, "in_save", 99, 99)
            current = controller.activity.snapshot(10)
            assert current["kind"] == "game_loading" and current["process_session"] != original_session
            controller.events.put(("bridge", (10, controller.sessions[10], {"type": "error", "description": "Owned bridge failure"})))
            with pytest.raises(lifecycle.LaunchError) as failure:
                controller.poll()
            diagnostics.handle_error(logger, failure.value)
    assert outcome["failure"]["activity"]["kind"] == "game_loading"
    line = next(line for line in path.read_text(encoding="utf-8").splitlines() if "Owned loading diagnostic" in line)
    assert json.loads(line.partition(" activity=")[2])["kind"] == "save_loading"
    delayed = next(line for line in path.read_text(encoding="utf-8").splitlines() if "Owned delayed loading diagnostic" in line)
    assert json.loads(delayed.partition(" activity=")[2])["generation"] == 0
