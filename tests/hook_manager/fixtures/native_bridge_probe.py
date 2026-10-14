# SPDX-License-Identifier: MPL-2.0
"""Run the real bridge against an owned target; copied beside entry.py before execution."""

import importlib.util, json, sys, time
from pathlib import Path

root = Path(__file__).resolve().parent.parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, root / "launcher" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frida = load("test_binding", "native_test_binding.py").load(root)
lifecycle = load("lifecycle", "process_lifecycle.py")
bridge = load("bridge", "game_bridge.py")
output = Path(sys.argv[3])
messages = []
# The bridge receives the installation folder beside this isolated launcher copy.
pack = root.parent / "data/owned pack"
pack.mkdir(parents=True)
controller = lifecycle.Controller(
    frida.get_local_device(),
    [sys.argv[1], "-B", sys.argv[2], str(output)],
    messages.append,
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
    assert any("HOOK MANAGER READY" in message for message in messages), messages
    current = next(iter(controller.bridges.values()))
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
    assert sum("PACK DISCOVERY" in message for message in messages) == 1, messages
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
print(json.dumps(messages))
