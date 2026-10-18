# SPDX-License-Identifier: MPL-2.0
"""Test monitoring ownership and the production native collector when built.

Set MODLAUNCHER_PROFILE_NATIVE to the trusted build directory to run native cases.
The optional backend stays outside the bridge and release until live acceptance.
"""

from contextlib import contextmanager
import sys
import threading
import time
from types import FunctionType, SimpleNamespace

import pytest

from hook_manager.profiling_collector import Capture, CollectorError, Limits, TOOL_ID


class Monitoring:
    """Owned fake slot with one-shot faults before or after an API mutation."""
    events = sys.monitoring.events

    def __init__(self):
        self.name, self.global_events = None, 0
        self.local, self.callbacks = {}, {}
        self.fault = None

    def use_tool_id(self, tool, name):
        if self.name is not None:
            raise ValueError("occupied")
        self.name = name

    def get_tool(self, tool):
        return self.name

    def get_events(self, tool):
        return self.global_events

    def get_local_events(self, tool, code):
        return self.local.get(id(code), 0)

    def _fault(self, operation, after):
        if self.fault == (operation, after):
            self.fault = None
            raise RuntimeError("injected monitoring failure")

    def register_callback(self, tool, event, callback):
        self._fault("callback", False)
        previous = self.callbacks.get(event)
        self.callbacks[event] = callback
        self._fault("callback", True)
        return previous

    def set_local_events(self, tool, code, mask):
        self._fault("local", False)
        self.local[id(code)] = mask
        self._fault("local", True)

    def set_events(self, tool, mask):
        self._fault("global", False)
        self.global_events = mask
        self._fault("global", True)

    def free_tool_id(self, tool):
        self._fault("free", False)
        self.name = None


class Counters:
    def __init__(self, *args):
        self.stopped = False

    def begin(self, *args): pass
    def end(self, *args): pass
    def unwind(self, *args): pass
    def start(self): pass
    def stop(self): self.stopped = True
    def boundary(self): return True
    def pause(self, value): pass

    def snapshot(self):
        return dict(rows=[], windows=[(0, 100, 20, 0)], failure="")


def selected():
    return 42


def portable():
    capture = Capture([selected.__code__], backend=SimpleNamespace(Collector=Counters))
    capture._monitoring = Monitoring()
    return capture


def test_occupied_slot_is_preserved():
    capture = portable()
    capture._monitoring.name = "unrelated"
    with pytest.raises(ValueError, match="occupied"):
        capture.start()
    assert capture._monitoring.name == "unrelated"


@pytest.mark.parametrize("kind", ["global", "local", "callback"])
def test_residual_registrations_are_preserved(kind):
    capture = portable()
    mon = capture._monitoring
    if kind == "global":
        mon.global_events = mon.events.PY_UNWIND
    elif kind == "local":
        mon.local[id(selected.__code__)] = mon.events.PY_START
    else:
        mon.callbacks[mon.events.PY_START] = selected
    with pytest.raises(CollectorError, match="residual"):
        capture.start()
    assert mon.name is None
    assert mon.global_events == (mon.events.PY_UNWIND if kind == "global" else 0)
    assert mon.get_local_events(TOOL_ID, selected.__code__) == (mon.events.PY_START if kind == "local" else 0)
    assert mon.callbacks.get(mon.events.PY_START) is (selected if kind == "callback" else None)


@pytest.mark.parametrize("operation,after", [("callback", False), ("local", False), ("local", True),
                                           ("global", False), ("global", True)])
def test_partial_activation_rolls_back_every_registration(operation, after):
    capture = portable()
    mon = capture._monitoring
    mon.fault = operation, after
    with pytest.raises(RuntimeError, match="injected"):
        capture.start()
    assert (not capture._native_started or capture._native.stopped) and mon.name is None
    assert not mon.global_events and not any(mon.local.values()) and not any(mon.callbacks.values())
    if capture._native_started:
        assert capture.snapshot()["failures"] == ["monitoring_activation_failed"]


@pytest.mark.parametrize("operation", ["global", "local", "callback", "free"])
def test_cleanup_fault_remains_incomplete_after_successful_retry(operation):
    capture = portable()
    capture.start()
    capture._monitoring.fault = operation, False
    with pytest.raises(CollectorError, match="cleanup|release"):
        capture.stop()
    assert capture._monitoring.name == capture._lease
    assert capture.snapshot()["incomplete"]
    capture.stop()
    assert capture._monitoring.name is None
    assert capture.snapshot()["failures"] == ["monitoring_cleanup_failed"]


def test_replacement_owner_is_untouched_and_lost_lease_is_explicit():
    capture = portable()
    capture.start()
    mon = capture._monitoring
    mon.name = "replacement"
    previous = dict(mon.callbacks)
    with pytest.raises(CollectorError, match="lost"):
        capture.stop()
    assert mon.name == "replacement" and mon.callbacks == previous
    assert capture.snapshot()["failures"] == ["monitoring_ownership_lost"]


def test_capture_is_one_shot_and_boundaries_are_consecutive():
    capture = portable()
    with pytest.raises(CollectorError, match="Stop"):
        capture.snapshot()
    capture.start()
    with pytest.raises(CollectorError, match="once"):
        capture.start()
    for value in (True, 1, 3):
        with pytest.raises(ValueError):
            capture.advance_lap(value)
    assert capture.advance_lap(2) and capture.finishing()
    with pytest.raises(ValueError):
        capture.advance_lap(3)
    with pytest.raises(CollectorError):
        capture.finishing()
    with pytest.raises(ValueError):
        capture.pause(1)
    capture.stop()
    capture.stop()


def test_active_selected_frame_is_refused_before_claiming_monitoring():
    capture = None
    def already_running():
        capture.start()
    capture = Capture([already_running.__code__], backend=SimpleNamespace(Collector=Counters))
    capture._monitoring = Monitoring()
    with pytest.raises(CollectorError, match="already active"):
        already_running()
    assert capture._monitoring.name is None


@pytest.mark.parametrize("codes", [[], [selected.__code__, selected.__code__], [object()],
                                    [compile("pass", "<module>", "exec")]])
def test_invalid_code_admission_precedes_backend_loading(codes):
    with pytest.raises(ValueError):
        Capture(codes, backend=object())


@contextmanager
def recording(native, *functions, limits=Limits()):
    """Restore monitoring even when a target call or assertion fails."""
    capture = Capture([function.__code__ for function in functions], limits=limits, backend=native)
    capture.start()
    try:
        yield capture
    finally:
        capture.stop()


def complete_snapshot(capture):
    """Check the accounting invariants shared by successful native captures."""
    data = capture.snapshot()
    assert not data["incomplete"], data["failures"]
    assert data["clock"] in ("QueryPerformanceCounter", "CLOCK_MONOTONIC")
    assert data["clock_resolution_ns"] >= 1
    for _, _, starts, ends, incoming, outgoing, truncated, exceptional, inclusive, exclusive in data["rows"]:
        assert incoming + starts == ends + truncated + outgoing
        assert exceptional <= ends and exclusive <= inclusive
    assert all(0 <= window["paused_ns"] <= window["wall_ns"] for window in data["windows"])
    return data


@pytest.mark.native
def test_native_cross_lap_and_finishing_partition_with_empty_windows(native):
    def child():
        return sum(range(100))

    def parent():
        child()
        assert capture.advance_lap(2)
        child()
        assert capture.finishing()
        child()
    with recording(native, parent, child) as capture:
        parent()
    data = complete_snapshot(capture)
    rows = {(record[0], record[1]): record[2:] for record in data["rows"]}
    assert rows[0, 0][:4] == (1, 0, 0, 1)
    assert rows[1, 0][:4] == (0, 0, 1, 1)
    assert rows[2, 0][:4] == (0, 1, 1, 0)
    for window in range(3):
        assert rows[window, 1][:2] == (1, 1)
        assert rows[window, 0][6] == rows[window, 0][7] + rows[window, 1][7]
    assert [window["lap"] for window in data["windows"]] == [1, 2, None]
    with recording(native, child) as empty:
        empty.advance_lap(2)
    data = complete_snapshot(empty)
    assert data["rows"] == [] and len(data["windows"]) == 2


@pytest.mark.native
def test_native_recursion_exceptions_and_capture_reuse_are_isolated(native):
    error = ValueError("original")
    def recursive(depth):
        if depth:
            return recursive(depth - 1)
        raise error
    for _ in range(3):
        with recording(native, recursive) as capture:
            with pytest.raises(ValueError) as caught:
                recursive(3)
            assert caught.value is error
        data = complete_snapshot(capture)
        assert data["rows"][0][2:8] == (4, 4, 0, 0, 0, 4)


@pytest.mark.native
def test_native_pause_crosses_boundary_without_losing_open_invocations(native):
    def parent():
        capture.pause(True)
        time.sleep(.015)
        capture.advance_lap(2)
        time.sleep(.015)
        capture.pause(False)
    with recording(native, parent) as capture:
        parent()
    data = complete_snapshot(capture)
    assert all(window["paused_ns"] >= 10_000_000 for window in data["windows"])
    active = sum(window["wall_ns"] - window["paused_ns"] for window in data["windows"])
    assert sum(row[8] for row in data["rows"]) <= active


@pytest.mark.native
def test_native_stop_inside_selected_function_truncates_reporting_only(native):
    def target():
        capture.stop()
        return 42
    with recording(native, target) as capture:
        assert target() == 42
    data = complete_snapshot(capture)
    assert data["rows"][0][2:7] == (1, 0, 0, 0, 1)


@pytest.mark.native
@pytest.mark.parametrize("limits", [Limits(stack_depth=2), Limits(windows=1)], ids=["stack", "windows"])
def test_native_exhaustion_preserves_game_results(native, limits):
    def recursive(depth):
        return recursive(depth - 1) if depth else 42

    with recording(native, recursive, limits=limits) as capture:
        if limits.windows == 1:
            assert not capture.advance_lap(2)
        assert recursive(8) == 42
    assert capture.snapshot()["failures"] == ["collector_overflow"]


@pytest.mark.native
def test_native_worker_capture_excludes_foreign_thread_and_preserves_other_tools(native):
    other = sys.monitoring
    other.use_tool_id(4, "owned-test-other-tool")
    calls = []
    event = other.events.PY_START
    other.register_callback(4, event, lambda *args: calls.append(1))
    other.set_local_events(4, selected.__code__, event)
    original = sys.getprofile()
    def sentinel(frame, event, arg): pass
    sys.setprofile(sentinel)
    ready, proceed = threading.Event(), threading.Event()
    result = {}
    def worker():
        try:
            with recording(native, selected) as capture:
                result["capture"] = capture
                ready.set()
                assert proceed.wait(5)
                for _ in range(7):
                    assert selected() == 42
            result["data"] = complete_snapshot(capture)
        except BaseException as error:
            result["error"] = error
        finally:
            ready.set()
    thread = threading.Thread(target=worker)
    try:
        thread.start()
        assert ready.wait(5)
        assert "error" not in result, result
        for _ in range(5):
            assert selected() == 42
        with pytest.raises(CollectorError, match="thread"):
            result["capture"].stop()
        proceed.set()
        thread.join(5)
        assert not thread.is_alive() and "error" not in result, result
        data = result["data"]
        assert data["rows"][0][2:4] == (7, 7) and data["ignored_thread_events"] == 10
        assert len(calls) == 12 and sys.getprofile() is sentinel
        assert other.get_tool(4) == "owned-test-other-tool"
    finally:
        proceed.set()
        thread.join(5)
        sys.setprofile(original)
        other.set_local_events(4, selected.__code__, 0)
        other.register_callback(4, event, None)
        other.free_tool_id(4)


@pytest.mark.native
def test_native_overflow_and_bad_event_fail_without_raising_into_target(native):
    counters = native.Collector((selected.__code__,), counter_limit=1)
    counters.start()
    counters.begin(selected.__code__, 0)
    counters.end(selected.__code__, 0, 42)
    counters.stop()
    assert counters.snapshot()["failure"] == "counter_overflow"
    counters = native.Collector((selected.__code__,))
    counters.start()
    counters.end(selected.__code__, 0, 42)
    counters.stop()
    assert counters.snapshot()["failure"] == "event_mismatch"


@pytest.mark.native
def test_resumable_codes_and_native_reinitialization_are_refused(native):
    def generator():
        yield 1

    async def coroutine():
        return 1

    async def async_generator():
        yield 1

    for function in (generator, coroutine, async_generator):
        with pytest.raises(ValueError):
            Capture([function.__code__], backend=native)
        with pytest.raises(ValueError):
            native.Collector((function.__code__,))
    collector = native.Collector((selected.__code__,))
    with pytest.raises(RuntimeError, match="configured"):
        collector.__init__((selected.__code__,))


@pytest.mark.native
def test_native_distinguishes_equal_code_objects_by_identity(native):
    original = selected.__code__
    duplicate = original.replace()
    assert original == duplicate and original is not duplicate
    other = FunctionType(duplicate, globals())
    with recording(native, selected, other) as capture:
        selected()
        other()
        other()
    data = complete_snapshot(capture)
    assert [row[2:4] for row in data["rows"]] == [(1, 1), (2, 2)]


@pytest.mark.native
def test_native_caught_child_exception_does_not_mark_parent_exceptional(native):
    error = ValueError("child")
    def child():
        raise error

    def parent():
        try:
            child()
        except ValueError as caught:
            assert caught is error
        return 42
    with recording(native, parent, child) as capture:
        assert parent() == 42
    data = complete_snapshot(capture)
    assert [row[7] for row in data["rows"]] == [0, 1]


@pytest.mark.native
def test_native_calls_during_pause_keep_counts_with_zero_elapsed(native):
    with recording(native, selected) as capture:
        capture.pause(True)
        selected()
        capture.advance_lap(2)
        selected()
    data = complete_snapshot(capture)
    assert all(row[2:4] == (1, 1) and row[8:] == (0, 0) for row in data["rows"])


@pytest.mark.native
def test_native_retired_thread_context_cannot_be_adopted_by_a_later_worker(native):
    counters = native.Collector((selected.__code__,))
    first = threading.Thread(target=counters.start)
    first.start()
    first.join(5)
    assert not first.is_alive()
    outcomes = []
    def later():
        # Sequential workers can receive the same OS/threading identifier. The
        # backend must still reject a different CPython thread-state generation.
        try:
            counters.stop()
        except RuntimeError:
            outcomes.append("refused")
        else:
            outcomes.append("adopted")
    for _ in range(4):
        thread = threading.Thread(target=later)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
    assert outcomes == ["refused"] * 4
    with pytest.raises(RuntimeError, match="Stop"):
        counters.snapshot()
