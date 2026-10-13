# SPDX-License-Identifier: MPL-2.0
"""Force reply/event ordering that cannot be scheduled reliably in live processes."""

import threading
from types import SimpleNamespace
import pytest
from launcher.native_worker import Session, Script, WorkerError


@pytest.mark.parametrize("target", ["session", "script"])
@pytest.mark.parametrize("retired", [False, True])
def test_detach_event_wins_over_an_older_state_reply(target, retired):
    # Replace only the RPC boundary; use the real proxies and their real event handler.
    worker = SimpleNamespace(
        failure=None,
        closed=False,
        state_lock=threading.RLock(),
        sessions={},
        scripts={},
    )
    session = Session(worker, 1, False)
    script = Script(worker, session, 2)
    worker.sessions[1], worker.scripts[2] = session, script
    requests = []

    def state_query(method, params):
        requests.append(method)
        # The event arrives while the caller awaits a previously produced reply.
        session._detached("process-terminated")
        if retired:
            raise WorkerError("Unknown native handle")
        return False

    worker.request = state_query
    if target == "session":
        assert session.is_detached
        assert session.is_detached
    else:
        assert script.is_destroyed
        assert script.is_destroyed
    assert requests == [f"{target}.state"]
    assert not worker.sessions and not worker.scripts
