# SPDX-License-Identifier: MPL-2.0
"""Own a native synchronous collector and its CPython monitoring registrations.

Trusted race adapters supply verified live code objects and boundary calls. Only
the thread calling start() is enrolled, allowing watched-main-thread and simulated
worker-thread captures. The native callbacks handle every measured invocation;
this module runs during preparation, boundaries, teardown and post-race export.
"""

from dataclasses import dataclass
import importlib
import inspect
import sys
import threading
from types import CodeType
import uuid


FIELDS = (
    "calls_started", "calls_completed", "carried_in", "carried_out",
    "calls_truncated", "exceptional_exits", "inclusive_ns", "boundary_exclusive_ns",
)
TOOL_ID = 3


class CollectorError(RuntimeError):
    """Capture admission, ownership or cleanup failed outside game callbacks."""


@dataclass(frozen=True)
class Limits:
    """Reserve this many windows (including finishing) and active measured frames.

    These are storage quotas, not an accepted slowdown or process-memory ceiling.
    Functions are separately limited to 256 unique synchronous code objects.
    """
    windows: int = 64
    stack_depth: int = 128

    def __post_init__(self):
        for value, maximum in ((self.windows, 512), (self.stack_depth, 1024)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("Expected 1..512 windows and 1..1024 stack depth")


class Capture:
    """Collect one race on one thread for a frozen set of live Python code objects.

    start() opens lap 1; advance_lap() accepts consecutive lap numbers; finishing()
    opens the unnumbered finishing window. pause() excludes elapsed paused time
    while preserving invocation counts. stop() checkpoints and truncates open
    calls; snapshot() then exports sparse counters and all observed windows.

    Ownership, function IDs and artifact checks belong to the supplying provider.
    Generators/coroutines and calls already active at start are refused. Foreign
    threads are outside declared coverage, with their selected event count exposed.
    The optional backend is the trusted native extension, injectable for tests.
    """

    def __init__(self, codes, *, limits=Limits(), backend=None):
        if (sys.implementation.name != "cpython" or sys.version_info < (3, 13)
                or not getattr(sys, "_is_gil_enabled", lambda: True)()):
            raise CollectorError("Requires CPython 3.13+ with the GIL enabled")
        if type(codes) not in (tuple, list) or not 1 <= len(codes) <= 256:
            raise ValueError("Select 1..256 unique synchronous code objects")
        if type(limits) is not Limits:
            raise ValueError("Expected collector Limits")
        resumable = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
        if any(type(code) is not CodeType or not code.co_flags & inspect.CO_NEWLOCALS
               or code.co_flags & resumable for code in codes):
            raise ValueError("Only synchronous function code is supported")
        if len({id(code) for code in codes}) != len(codes):
            raise ValueError("Duplicate code object")
        self.codes = tuple(codes)
        self.limits = limits
        if backend is None:
            try:
                backend = importlib.import_module("_race_profile")
            except ImportError as error:
                raise CollectorError("Native race collector is unavailable") from error
        self._native = backend.Collector(self.codes, limits.windows, limits.stack_depth)
        self._monitoring = sys.monitoring
        events = self._monitoring.events
        self._callbacks = ((events.PY_START, self._native.begin),
                           (events.PY_RETURN, self._native.end),
                           (events.PY_UNWIND, self._native.unwind))
        self._lease = "modLauncher.race." + uuid.uuid4().hex
        self._claimed = self._attempted = self._native_started = self._stopped = False
        self._owner = None
        self._owner_thread = None
        self._previous_callbacks, self._local_codes = [], []
        self._global_events = False
        self._failures = []
        self._labels = [{"kind": "lap", "lap": 1}]

    def _thread(self):
        if threading.current_thread() is not self._owner_thread:
            raise CollectorError("Control capture on its enrolled thread")

    def _owns_tool(self):
        return self._monitoring.get_tool(TOOL_ID) == self._lease

    def _control(self):
        self._thread()
        if not self._native_started or self._stopped or not self._owns_tool():
            raise CollectorError("Capture is stopped or its monitoring lease was lost")

    def start(self):
        """Reserve the tool and activate callbacks before any selected call begins."""
        if self._attempted:
            raise CollectorError("A capture can start only once")
        # Monitoring supplies exits for pre-existing calls without supplying their
        # starts. Refuse late attachment instead of inventing invocation counts.
        selected = {id(code) for code in self.codes}
        frame = sys._getframe()
        try:
            while frame is not None:
                if id(frame.f_code) in selected:
                    raise CollectorError("Selected calls are already active")
                frame = frame.f_back
        finally:
            del frame
        self._attempted = True
        self._owner = threading.get_ident()
        self._owner_thread = threading.current_thread()
        mon, events = self._monitoring, self._monitoring.events
        try:
            mon.use_tool_id(TOOL_ID, self._lease)
            self._claimed = True
            # A freed ID can retain events/callbacks. Preserve and refuse residue
            # rather than adopting another tool's abandoned registrations.
            if mon.get_events(TOOL_ID) or any(mon.get_local_events(TOOL_ID, code) for code in self.codes):
                raise CollectorError("Monitoring slot has residual events")
            for event, callback in self._callbacks:
                previous = mon.register_callback(TOOL_ID, event, callback)
                self._previous_callbacks.append((event, previous))
                if previous is not None:
                    raise CollectorError("Monitoring slot has a residual callback")
            self._native.start()
            self._native_started = True
            for code in self.codes:
                self._local_codes.append(code)
                mon.set_local_events(TOOL_ID, code, events.PY_START | events.PY_RETURN)
            # Unwind cannot be enabled locally; the native lookup filters it to
            # the selected code identities and the enrolled thread.
            self._global_events = True
            mon.set_events(TOOL_ID, events.PY_UNWIND)
        except BaseException:
            self._failures[:] = ["monitoring_activation_failed"]
            self.stop()
            raise

    def _cleanup(self):
        if not self._claimed:
            return
        if not self._owns_tool():
            self._failures[:] = ["monitoring_ownership_lost"]
            raise CollectorError("Monitoring lease was lost; its replacement was left untouched")
        mon = self._monitoring
        errors = []
        # Try every owned restoration even when one action fails. Keep the lease
        # reserved on failure so residual callbacks cannot be adopted by a retry.
        actions = []
        if self._global_events:
            actions.append((mon.set_events, (TOOL_ID, 0)))
        actions.extend((mon.set_local_events, (TOOL_ID, code, 0)) for code in self._local_codes)
        actions.extend((mon.register_callback, (TOOL_ID, event, previous))
                       for event, previous in reversed(self._previous_callbacks))
        for function, arguments in actions:
            try:
                function(*arguments)
            except Exception as error:
                errors.append(error)
        if errors:
            self._failures[:] = ["monitoring_cleanup_failed"]
            raise CollectorError("Monitoring cleanup failed; lease retained for retry") from errors[0]
        try:
            mon.free_tool_id(TOOL_ID)
        except Exception as error:
            self._failures[:] = ["monitoring_cleanup_failed"]
            raise CollectorError("Monitoring lease release failed; retry cleanup") from error
        self._claimed = False

    def advance_lap(self, lap):
        """Checkpoint to the next consecutive numbered lap; return false on overflow."""
        self._control()
        previous = self._labels[-1]["lap"]
        if type(lap) is not int or previous is None or lap != previous + 1:
            raise ValueError("Expected the next consecutive lap number")
        if not self._native.boundary():
            return False
        self._labels.append({"kind": "lap", "lap": lap})
        return True

    def finishing(self):
        """Checkpoint post-lap race work into a separate unnumbered window."""
        self._control()
        if self._labels[-1]["lap"] is None:
            raise CollectorError("Finishing window already open")
        if not self._native.boundary():
            return False
        self._labels.append({"kind": "finishing", "lap": None})
        return True

    def pause(self, paused):
        self._control()
        if type(paused) is not bool:
            raise ValueError("Expected a pause boolean")
        self._native.pause(paused)

    def stop(self):
        """Stop timing before restoring instrumentation; cleanup can be retried."""
        if self._owner is None:
            return
        self._thread()
        if self._native_started and not self._stopped:
            self._native.stop()
            self._stopped = True
        self._cleanup()

    def snapshot(self):
        """Export after stop; raw uint64 values remain Python integers internally."""
        if not self._stopped:
            raise CollectorError("Stop capture before exporting")
        data = self._native.snapshot()
        data["windows"] = [dict(self._labels[index], start_ns=start, wall_ns=wall, paused_ns=paused)
                           for index, start, wall, paused in data["windows"]]
        failure = data.pop("failure")
        data["failures"] = ([failure] if failure else []) + self._failures
        data["incomplete"] = bool(data["failures"])
        data["fields"] = FIELDS
        data["collector"] = "native-cpython-sync-1"
        data["enrolled_thread"] = self._owner
        return data
