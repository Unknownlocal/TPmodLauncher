# SPDX-License-Identifier: MPL-2.0
"""Cancellation must finish before shared code can serve the next sandbox."""

import threading
import time
from types import SimpleNamespace

from launcher.deadline_watchdog import DeadlineWatchdog


def test_cancellation_waits_for_interruption_and_old_tokens_cannot_disarm_reuse():
    entered, release, cancelled, started, next_expiry = (
        threading.Event() for _ in range(5)
    )

    def interrupt():
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
        else:
            next_expiry.set()

    watchdog = DeadlineWatchdog(SimpleNamespace(increment_epoch=interrupt))
    waiter = None
    try:
        old = watchdog.arm(time.monotonic() - 1)
        thread = watchdog.thread
        assert entered.wait(2)

        def cancel():
            started.set()
            watchdog.cancel(old)
            cancelled.set()

        waiter = threading.Thread(target=cancel)
        waiter.start()
        assert started.wait(2)
        # The native interruption is still blocked: cancellation must not return.
        assert not cancelled.wait(.02)
        release.set()
        assert cancelled.wait(2)

        # Keep the watchdog asleep until stale cancellation has happened, so
        # this cannot pass just because the new timeout was already handled.
        with watchdog.condition:
            watchdog.arm(time.monotonic() - 1)
            watchdog.cancel(old)
        assert next_expiry.wait(2)
        assert watchdog.thread is thread
    finally:
        release.set()
        if waiter is not None:
            waiter.join(2)
        watchdog.close()
    assert not thread.is_alive()
