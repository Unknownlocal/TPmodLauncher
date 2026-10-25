# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for threads."""

import threading

import pytest

from hook_manager.callables import CallBinding, refuse_boolean
from hook_manager.manager import HookError

from .support import approve, project


def test_worker_enrollment_poll_and_mutation_exclusion(host):
    mutations, results = [], []
    entered, release = threading.Event(), threading.Event()
    module, engine = host()
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", project, refuse_boolean)])

    def worker():
        with engine.operation():
            entered.set()
            assert release.wait(3)
            results.append(module.method(7))

    thread = threading.Thread(target=worker)
    engine.enroll(thread)
    thread.start()
    try:
        assert entered.wait(3)
        assert engine.poll()["owners"] == {"owner": "ready"}
        with pytest.raises(HookError, match="during an operation"):
            engine.remove_owner("owner")
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive() and results == [True] and mutations == [7]
    unknown = threading.Thread(target=lambda: results.append(module.method(8)))
    unknown.start()
    unknown.join(3)
    assert results == [True, False] and mutations == [7]


def test_registration_excludes_worker_admission_until_publication(host, monkeypatch):
    module, engine = host()
    module.method = lambda value: True
    approve(engine, module)
    attempted, admitted = threading.Event(), threading.Event()
    results = []

    def worker():
        attempted.set()
        with engine.operation():
            admitted.set()
            results.append(module.method(1))

    thread = threading.Thread(target=worker)
    engine.enroll(thread)
    original_install = engine._install_ready

    def install(name):
        thread.start()
        assert attempted.wait(3)
        assert not admitted.wait(.02), "Worker entered a partial registration"
        original_install(name)

    monkeypatch.setattr(engine, "_install_ready", install)
    try:
        engine.register("owner", [CallBinding("rule", "method", project, refuse_boolean)])
    finally:
        thread.join(3)
    assert not thread.is_alive() and results == [True] and engine.owners == {"owner": "ready"}


def test_waiting_worker_cannot_enter_a_closed_engine(host):
    _, engine = host()
    attempted, results = threading.Event(), []

    def worker():
        attempted.set()
        try:
            with engine.operation():
                results.append("entered")
        except HookError:
            results.append("rejected")

    thread = threading.Thread(target=worker)
    engine.enroll(thread)
    with engine._lock:
        thread.start()
        assert attempted.wait(3)
        engine.stop()
    thread.join(3)
    assert not thread.is_alive() and results == ["rejected"]


def test_escaped_removed_wrapper_does_not_hold_lock_while_native_joins_worker(host):
    module, engine = host()
    module.second = lambda value: True
    completed, threads = [], []

    def method(value):
        thread = threading.Thread(target=lambda: completed.append(module.second(value)))
        threads.append(thread)
        engine.enroll(thread)
        original_run = thread.run

        def run():
            with engine.operation():
                original_run()

        thread.run = run
        thread.start()
        thread.join(.2)
        return not thread.is_alive()

    module.method = method
    approve(engine, module)
    approve(engine, module, "second")
    engine.register("first", [CallBinding("rule", "method", project, refuse_boolean)])
    engine.register("second", [CallBinding("rule", "second", project, refuse_boolean)])
    escaped = module.method
    engine.remove_owner("first")
    try:
        assert escaped(1), "Removed wrapper held registry lock across a native join"
    finally:
        for thread in threads:
            thread.join(3)
    assert completed == [True]
