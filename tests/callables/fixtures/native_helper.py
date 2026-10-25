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
# The real adapter owner restores guard registries and defers busy shutdown.
guards = host.CallableAdapters()
host.adapters.regulations.append(guards)
guards.evaluate = lambda owner, batch, deadline: [{"decision": "deny", "message": "Owned guard"}]
guards.approve(host.CallableTarget(
    "owned.guard", "__main__", (), "guarded", "(value)",
))
guards.register("owned.guard", [host.CallBinding(
    "rule", "owned.guard", lambda arguments, result: {"value": arguments["value"]},
    host.ResultContract({"shape": "bool", "denied": {"literal": False}}),
)])
assert guarded(1) is False and calls == []
guards.evaluate = lambda owner, batch, deadline: [{"decision": "allow"}]


def worker():
    with guards.operation():
        assert guarded(2) is True


thread = threading.Thread(target=worker)
guards.enroll(thread)
thread.start()
thread.join(2)
assert not thread.is_alive() and calls == [2]
(output / "active.json").write_text(json.dumps(guards.status()))

# Observe an actual bridge stop attempt, then let the operation finish. This
# makes the overlap deterministic without a timing-dependent sleep in the worker.
wait_seen = threading.Event()
close_adapters = host.adapters.close


def observe_stop():
    stopped = close_adapters()
    if not stopped:
        wait_seen.set()
    return stopped


host.adapters.close = observe_stop


def shutdown_worker():
    with guards.operation():
        (output / "shutdown-ready").touch()
        assert wait_seen.wait(5), "Bridge did not defer active shutdown"


shutdown = None
while not host.manager.closed and time.monotonic() < deadline:
    if (output / "shutdown-request").exists() and shutdown is None:
        shutdown = threading.Thread(target=shutdown_worker)
        guards.enroll(shutdown)
        shutdown.start()
    time.sleep(.02)
if shutdown is not None:
    shutdown.join(1)
assert host.manager.closed and guards.closed
assert guarded is original and guarded(3) is True and calls == [2, 3]
assert wait_seen.is_set() and shutdown is not None and not shutdown.is_alive()
assert guards.finder not in sys.meta_path
assert not guards.errors
(output / "closed.json").write_text(json.dumps(guards.status()))
