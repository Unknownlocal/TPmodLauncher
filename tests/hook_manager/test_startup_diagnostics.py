# SPDX-License-Identifier: MPL-2.0
"""Keep partial bootstrap timings when trusted source loading fails."""

import json
import sys
from types import SimpleNamespace

import pytest

from hook_manager.bootstrap import pulse
from hook_manager.bootstrap_readiness import bootstrap_readiness


@pytest.mark.parametrize("state,expected", [
    ("missing", "WAIT: threading is not ready"),
    ("initialising", "WAIT: threading is not ready"),
    ("worker", "WAIT: other thread"),
    ("importing", "WAIT: reporting dependency is importing: socket"),
    ("ready", "READY"),
])
def test_readiness_gate_uses_existing_thread_and_import_state(monkeypatch, state, expected):
    main, worker = object(), object()
    threads = SimpleNamespace(__spec__=SimpleNamespace(_initializing=state == "initialising"),
                              current_thread=lambda: worker if state == "worker" else main,
                              main_thread=lambda: main)
    # Restore real interpreter modules before pytest or any other code runs.
    with monkeypatch.context() as patch:
        if state == "missing":
            patch.delitem(sys.modules, "threading", raising=False)
        else:
            patch.setitem(sys.modules, "threading", threads)
        patch.setitem(sys.modules, "socket", SimpleNamespace(__spec__=SimpleNamespace(_initializing=state == "importing")))
        result = bootstrap_readiness()
    assert result == expected


def test_failed_bootstrap_identifies_source_and_keeps_completed_elapsed_wait(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 13, 0))
    monkeypatch.delitem(sys.modules, "_tpm_hook_manager_host", raising=False)
    good = "modLauncher/hook_manager/owned_wait.py"
    bad = "modLauncher/hook_manager/owned_failure.py"
    result = json.loads(pulse({good: "import time; time.sleep(.03)",
                               bad: "raise RuntimeError('Owned source initialisation failed')"},
                              "owned-startup-digest", "owned-startup-instance", ".", '{"operation":"status"}'))
    assert result["event"] == "error" and result["detail"] == "Owned source initialisation failed"
    assert bad in result["traceback"]
    assert result["startup"][0]["phase"] == "trusted source " + good
    assert result["startup"][0]["seconds"] >= .025
    assert result["startup"][1] == {"phase": "trusted source " + bad, "state": "failed"}
    assert "_tpm_hook_manager_host" not in sys.modules
