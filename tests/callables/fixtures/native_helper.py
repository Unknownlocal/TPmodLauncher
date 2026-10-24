# SPDX-License-Identifier: MPL-2.0
"""Owned CPython target for private guard and overlapping shutdown acceptance."""

import json
from pathlib import Path
import sys
import threading
import time

output = Path(sys.argv[1])
deadline = time.monotonic() + 15
while "_tpm_hook_manager_host" not in sys.modules and time.monotonic() < deadline:
    time.sleep(.02)
host = sys.modules["_tpm_hook_manager_host"]
calls = []


def guarded(value):
    calls.append(value)
    return True


original = guarded
host.callables.evaluate = lambda owner, batch, deadline: [{"decision": "deny", "message": "Owned guard"}]
host.callables.approve(host.CallableTarget(
    "owned.guard", "__main__", (), "guarded", "(value)",
))
host.callables.register("owned.guard", [host.CallBinding(
    "rule", "owned.guard", lambda arguments, result: {"value": arguments["value"]},
    host.ResultContract({"shape": "bool", "denied": {"literal": False}}),
)])
assert guarded(1) is False and calls == []
host.callables.evaluate = lambda owner, batch, deadline: [{"decision": "allow"}]


def worker():
    with host.callables.operation():
        assert guarded(2) is True


thread = threading.Thread(target=worker)
host.callables.enroll(thread)
thread.start()
thread.join(2)
assert not thread.is_alive() and calls == [2]
(output / "active.json").write_text(json.dumps(host.callables.status()))

# Observe an actual bridge stop attempt, then let the operation finish. This
# makes the overlap deterministic without a timing-dependent sleep in the worker.
wait_seen = threading.Event()
try_stop = host.callables.try_stop


def observe_stop():
    stopped = try_stop()
    if not stopped:
        wait_seen.set()
    return stopped


host.callables.try_stop = observe_stop


def shutdown_worker():
    with host.callables.operation():
        (output / "shutdown-ready").touch()
        assert wait_seen.wait(5), "Bridge did not defer active shutdown"


shutdown = None
while not host.manager.closed and time.monotonic() < deadline:
    if (output / "shutdown-request").exists() and shutdown is None:
        shutdown = threading.Thread(target=shutdown_worker)
        host.callables.enroll(shutdown)
        shutdown.start()
    time.sleep(.02)
if shutdown is not None:
    shutdown.join(1)
assert host.manager.closed and host.callables.closed
assert guarded is original and guarded(3) is True and calls == [2, 3]
assert wait_seen.is_set() and shutdown is not None and not shutdown.is_alive()
assert host.callables.finder not in sys.meta_path
assert not host.callables.errors
(output / "closed.json").write_text(json.dumps(host.callables.status()))
