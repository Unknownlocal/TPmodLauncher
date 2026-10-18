# SPDX-License-Identifier: MPL-2.0
"""Bind a data-only catalogue selection to one runtime's synchronous collector.

Trusted providers register verified code/functions locally and send only opaque
tokens to the launcher catalogue. At race entry, a Coordinator validates that
selection, reserves one physical plan and freezes its identities before start.
This module neither discovers game boundaries nor installs a public mod API.

Coordinator state is prepared -> running -> stopped -> finished. Binding loss,
last-consumer release or failed start can reach stopped before the race ends.
Stopped disables collection; only finish permits export and releases the runtime
reservation after successful cleanup. Native invocation callbacks never enter
this module: its work happens at admission, controls and post-race export.
"""

from copy import deepcopy
from dataclasses import dataclass
import inspect
import json
import threading
from types import CodeType, FunctionType
import uuid

from .manager import data_copy, identifier
from .profiling_collector import Capture, CollectorError, FIELDS, Limits


class PlanError(ValueError):
    """Carry a stable admission/lifecycle error code alongside readable detail."""
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def _json(value):
    """Freeze plain metadata into deterministic text for comparison and copying."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _function_id(value):
    """Accept catalogue fingerprint IDs, rather than public named identifiers."""
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Expected a catalogue function ID")
    return value


@dataclass(frozen=True, eq=False)
class _Binding:
    # eq=False keeps equality tied to this registration instance. Structurally
    # equal code objects or closures must not impersonate a retained binding.
    token: str
    code: CodeType
    functions: tuple


class Bindings:
    """One interpreter/process generation's private live bindings.

    Admission must already have verified artifact ownership. Function names,
    filenames and __wrapped__ do not establish it. Replacements/removals notify
    the reserved coordinator before publication. Namespace replacement must be
    reported by the provider; in-place function.__code__ changes are also checked
    at explicit control checkpoints. No lookup occurs on invocation callbacks.
    """

    def __init__(self, process, runtime, *, runtime_kind="game-python", maximum=4096):
        self.process, self.runtime = identifier(process), identifier(runtime)
        self.runtime_kind = identifier(runtime_kind)
        if type(maximum) is not int or not 1 <= maximum <= 4096:
            raise ValueError("Expected 1..4096 runtime bindings")
        self.maximum = maximum
        # Entries own live references; lease owns the one reserved coordinator.
        # Neither is persisted or sent to the launcher with the opaque token.
        self._entries, self._lease = {}, None
        self._owner = threading.current_thread()

    def _thread(self):
        # Compare Thread objects, since a later worker can reuse an OS thread ID.
        if threading.current_thread() is not self._owner:
            raise PlanError("wrong_context", "Control bindings on their owning runtime thread")

    def admit(self, function_id, code, functions):
        """Retain verified live functions and return a data-only binding token.

        The provider writes this token to Catalogue.bind, whose generation is
        copied into the race plan. Equal code with different object identity is
        a replacement. Multiple closures of one code share invocation accounting.
        """
        self._thread()
        _function_id(function_id)
        resumable = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
        # Module/class bodies lack function-local execution semantics. Resumable
        # functions need suspension accounting that this collector cannot supply.
        if type(code) is not CodeType or not code.co_flags & inspect.CO_NEWLOCALS or code.co_flags & resumable:
            raise ValueError("Expected synchronous function code")
        if (type(functions) not in (tuple, list) or not 1 <= len(functions) <= 64
                or any(type(f) is not FunctionType or f.__code__ is not code for f in functions)
                or len({id(f) for f in functions}) != len(functions)):
            raise ValueError("Expected 1..64 distinct verified functions of this code")
        previous = self._entries.get(function_id)
        if previous and previous.code is code and {id(f) for f in previous.functions} == {id(f) for f in functions}:
            # Re-observing the same live functions, in any order, is not a new
            # registration and must not advance the catalogue's generation.
            return previous.token
        if previous is None and len(self._entries) >= self.maximum:
            # Replacements reuse their existing slot; only new IDs consume quota.
            raise PlanError("resource_limit", "Runtime binding limit")
        if any(key != function_id and entry.code is code for key, entry in self._entries.items()):
            # Monitoring identifies code, not which alias/owner invoked it. Check
            # all registrations so a narrower selection cannot hide ambiguity.
            raise PlanError("ambiguous_binding", "One code identity cannot have multiple attributed functions")
        if self._lease:
            # Stop affected collection before publishing replacement state. A
            # cleanup exception leaves the old registration available for retry.
            self._lease._binding_changing(function_id)
        binding = _Binding(uuid.uuid4().hex, code, tuple(functions))
        self._entries[function_id] = binding
        return binding.token

    def remove(self, function_id):
        """Remove a known binding after notifying its capture; unknown IDs are no-ops."""
        self._thread()
        _function_id(function_id)
        if function_id in self._entries:
            if self._lease:
                self._lease._binding_changing(function_id)
            del self._entries[function_id]

    def _current(self, function_id, binding):
        # Token replacement changes the retained registration object. Mutating
        # function.__code__ keeps that object but fails the second identity check.
        return (self._entries.get(function_id) is binding
                and all(f.__code__ is binding.code for f in binding.functions))


@dataclass(frozen=True)
class _Target:
    # Canonical JSON freezes nested metadata and permits independent result copies.
    function_id: str
    generation: str
    record: str
    binding: _Binding


@dataclass(frozen=True)
class _Plan:
    targets: tuple
    missing: tuple
    metrics: tuple
    coverage: str

    def signature(self):
        # Missing coverage and metric policy affect measurement semantics too.
        # Consumer/delivery identity is omitted because it changes no collection.
        # Binding identity is opaque. Never compare code objects by equality.
        return (tuple((t.record, t.binding.token) for t in self.targets),
                self.missing, self.metrics, self.coverage)


class Coordinator:
    """Reserve one plan for a trusted race/context, with owned consumer handles.

    Acquire only before start, on the execution thread, using fresh trusted
    Catalogue.resolve data. The provider must synchronize catalogue support and
    binding changes with this registry; a saved selection is not a capability to
    override current admission policy. One registry/coordinator is shared by all
    consumers of a runtime/race. The race adapter calls start before selected
    work, controls at observed boundaries, and finish at completion/cancellation. Request
    scheduling, public idempotency keys, guest runtimes and result transport are
    outside this layer. A new race uses a new coordinator and the same registry.
    """

    def __init__(self, bindings, race, context, adapter, mode, *, limits=Limits(),
                 consumers=32, capture_factory=Capture):
        if type(bindings) is not Bindings or type(limits) is not Limits:
            raise ValueError("Expected runtime Bindings and collector Limits")
        bindings._thread()
        self.race, self.context, self.adapter = map(identifier, (race, context, adapter))
        if mode not in ("watched", "simulated") or type(consumers) is not int or not 1 <= consumers <= 64:
            raise ValueError("Expected race mode and 1..64 consumers")
        self.bindings, self.mode, self.limits = bindings, mode, limits
        self._maximum, self._factory = consumers, capture_factory
        self.capture_id = uuid.uuid4().hex
        self._plan, self._capture, self._data = None, None, None
        # handle -> (trusted owner, requested delivery, released). Released
        # entries stay reserved, bounding total handle churn for this race.
        self._consumers = {}
        # Window is the native zero-based index, not the public lap ordinal;
        # finishing consumes an index even though its lap label is None.
        self._state, self._window = "prepared", 0
        self._failures, self._losses = [], []
        self._outcome = None

    def _resolve(self, selection, metrics, coverage):
        """Build a candidate plan without reserving handles or installing callbacks."""
        if type(metrics) not in (tuple, list) or list(metrics) not in (["calls"], ["calls", "elapsed"]):
            raise ValueError("Expected calls, optionally followed by elapsed")
        if coverage not in ("require_all", "allow_partial"):
            raise ValueError("Expected require_all or allow_partial coverage")
        if (type(selection) is not dict or set(selection) != {"process", "functions", "unmatched"}
                or selection["process"] != self.bindings.process):
            # Identical source/function IDs can survive a process restart; its
            # live tokens and execution context cannot be adopted by this one.
            raise PlanError("stale_catalogue", "Selection belongs to a different process")
        functions, unmatched = selection["functions"], selection["unmatched"]
        if (type(functions) is not list or len(functions) > 256
                or type(unmatched) is not list or len(unmatched) > 16):
            raise PlanError("resource_limit", "Selection limits exceeded")
        missing = {_json({"selector": data_copy(s), "reason": "unknown_target"}) for s in unmatched}
        targets, seen = [], set()
        for row in functions:
            record = data_copy(row)
            function_id = _function_id(record["function_id"])
            if function_id in seen:
                raise ValueError("Duplicate selected function")
            seen.add(function_id)
            # Tokens verify the runtime binding but are not source metadata in
            # exported function records. Generations remain canonical strings so
            # uint64 values survive later JSON/JavaScript transport exactly.
            token = record.pop("binding_token")
            generation = record["binding_generation"]
            if (type(generation) is not str or not generation.isascii() or not generation.isdecimal()
                    or str(int(generation)) != generation or not 0 <= int(generation) < 2**64):
                raise ValueError("Expected canonical uint64 binding generation")
            binding = self.bindings._entries.get(function_id)
            reason = None
            # Preserve the first applicable coverage failure: unavailable source
            # support, another runtime, unsupported metrics, then stale live code.
            # In particular, a known guest target is not host-Python coverage.
            if record["availability"] != "ready":
                reason = record["reason"] or "not_ready"
            elif record["runtime_kind"] != self.bindings.runtime_kind:
                reason = "unsupported_runtime"
            elif not set(metrics) <= set(record["metrics"]) or record["kind"] != "function":
                reason = "unsupported_metric"
            elif not binding or binding.token != token or not self.bindings._current(function_id, binding):
                reason = "binding_changed"
            if reason:
                # Missing is declared separately, never encoded as a zero-call
                # success row. allow_partial can retain the other usable targets.
                missing.add(_json({"function": record, "reason": reason}))
                continue
            targets.append(_Target(function_id, generation, _json(record), binding))
        if missing and coverage == "require_all":
            raise PlanError("not_ready", "Required targets are unresolved or unsupported")
        if not targets:
            # Even allow_partial requires something that can actually be observed.
            raise PlanError("unknown_target", "No usable selected bindings")
        # Selector spelling/order must not change native indices or compatibility.
        targets.sort(key=lambda target: target.function_id)
        return _Plan(tuple(targets), tuple(sorted(missing)), tuple(metrics), coverage)

    def acquire(self, owner, selection, *, metrics=("calls", "elapsed"), coverage="require_all", delivery="summary"):
        """Reserve a compatible consumer before race start; never widen a plan."""
        self.bindings._thread()
        owner = identifier(owner)
        if self._state != "prepared":
            # A late request belongs to a future race at the API/provider layer;
            # it cannot widen this capture after its execution has begun.
            raise PlanError("not_ready", "Admission must precede race start")
        if delivery not in ("summary", "full"):
            raise ValueError("Expected summary or full delivery")
        if len(self._consumers) >= self._maximum:
            raise PlanError("resource_limit", "Consumer reservation limit")
        plan = self._resolve(selection, metrics, coverage)
        if ((self._plan and self._plan.signature() != plan.signature())
                or self.bindings._lease not in (None, self)):
            # Check both this coordinator's frozen plan and the runtime-wide
            # reservation. Extra measured children would alter exclusive time.
            raise PlanError("incompatible_capture", "Runtime already has a different reserved plan")
        # Commit only after validation/compatibility succeeds. The physical
        # collector is still absent; all compatible consumers start it together.
        handle = uuid.uuid4().hex
        self._consumers[handle] = (owner, delivery, False)
        self._plan, self.bindings._lease = plan, self
        return handle

    def _consumer(self, owner, handle):
        """Resolve a live owned handle without revealing another owner's state."""
        consumer = self._consumers.get(handle) if type(handle) is str else None
        if not consumer or consumer[0] != owner or consumer[2]:
            raise PlanError("unknown_handle", "Unknown or released consumer handle")
        return consumer

    def start(self):
        """Recheck reserved bindings, then install the single physical collector."""
        self.bindings._thread()
        if self._state != "prepared" or self._plan is None:
            raise PlanError("not_ready", "No prepared capture")
        if not self.check():
            # Admission retained references, but replacement could have happened
            # before race entry. Refuse instead of silently following new code.
            raise PlanError("not_ready", "Bindings changed before race entry")
        try:
            self._capture = self._factory([t.binding.code for t in self._plan.targets], limits=self.limits)
            self._capture.start()
        except BaseException:
            # A constructor can fail before native storage exists; start can fail
            # after partial monitoring activation. Preserve either failure and
            # the reservation so finish can retry any necessary cleanup.
            self._state = "stopped"
            self._failures.append("collector_start_failed")
            raise
        self._state = "running"

    def _halt(self, reason=None):
        """Stop collection without treating this checkpoint as race completion."""
        if reason and reason not in self._failures:
            self._failures.append(reason)
        # Set state before cleanup so controls cannot resume after a partial stop.
        # Retain the runtime reservation if cleanup raises, allowing a retry.
        self._state = "stopped"
        if self._capture is not None:
            self._capture.stop()

    def _binding_changing(self, function_id):
        # Before start, check() will refuse stale reservations. After stopping,
        # replacements must not rewrite retained results. Only running selected
        # targets need immediate interruption; unrelated changes leave them alone.
        if self._state == "running":
            for target in self._plan.targets:
                if target.function_id == function_id:
                    self._lost(target)
                    break

    def _lost(self, target):
        # Keep the frozen generation/window where coverage ended. This collector
        # stops the whole plan conservatively, including unaffected targets.
        self._losses.append(dict(function_id=target.function_id,
                                 binding_generation=target.generation, window=self._window))
        self._halt("binding_changed")

    def check(self):
        """Observe binding validity at a trusted control checkpoint, never per call."""
        self.bindings._thread()
        if self._state not in ("prepared", "running") or self._plan is None:
            # Controls can observe an already stopped capture without restarting
            # it or duplicating the original binding-loss record.
            return False
        for target in self._plan.targets:
            if not self.bindings._current(target.function_id, target.binding):
                self._lost(target)
                return False
        return True

    def advance_lap(self, lap):
        """Advance a running capture; return false after loss or native exhaustion."""
        if not self.check():
            return False
        if self._state != "running":
            # A valid prepared plan passes check(), but has no open race window.
            raise PlanError("not_ready", "Capture has not started")
        changed = self._capture.advance_lap(lap)
        # Failed native boundaries open no window, so loss attribution stays put.
        self._window += bool(changed)
        return changed

    def finishing(self):
        """Open the unnumbered finishing window using the same validity checks."""
        if not self.check():
            return False
        if self._state != "running":
            raise PlanError("not_ready", "Capture has not started")
        changed = self._capture.finishing()
        self._window += bool(changed)
        return changed

    def pause(self, paused):
        """Change pause state while running; a stopped/lost capture remains stopped."""
        if self.check():
            if self._state != "running":
                raise PlanError("not_ready", "Capture has not started")
            self._capture.pause(paused)

    def release(self, owner, handle):
        """Release one handle; only the last live consumer stops instrumentation."""
        self.bindings._thread()
        consumer = self._consumer(owner, handle)
        self._consumers[handle] = (*consumer[:2], True)
        # Finished results stay frozen. Earlier last-release stops collection but
        # still waits for the adapter's race-end finish before allowing export.
        if not any(not entry[2] for entry in self._consumers.values()) and self._state != "finished":
            self._halt("consumer_released")

    def finish(self, outcome="finished"):
        """End the race, retry cleanup if needed, then make retained data available."""
        self.bindings._thread()
        if outcome not in ("finished", "cancelled"):
            raise ValueError("Expected a finished or cancelled race")
        if self._state == "finished":
            # Successful finalization is one-shot; retries preserve its outcome.
            return
        if self._plan is None:
            raise PlanError("not_ready", "No reserved capture")
        if self._state in ("prepared", "running"):
            self.check()
        if self._state == "prepared":
            # A reserved but never-started race has no invented windows/timings.
            self._failures.append("capture_not_started")
        self._halt()
        self._data = None
        if self._capture is not None:
            try:
                self._data = self._capture.snapshot()
            except CollectorError:
                if "collector_start_failed" not in self._failures:
                    # Unexpected export errors remain retryable and keep the
                    # reservation; do not replace available data with success.
                    raise
                # Failed activation may never have started native collection, so
                # its snapshot is unavailable. Export the explicit failure only.
        # Publish finished state and release the lease only after cleanup and
        # snapshot succeed (or the failed-start/no-snapshot case is accounted for).
        self._outcome, self._state = outcome, "finished"
        self.bindings._lease = None

    def snapshot(self, owner, handle):
        """Return an independent internal result after race end, with frozen IDs.

        This is not the public paged schema or summary projector. Delivery choice
        is retained for that later dispatcher and does not affect instrumentation.
        """
        self.bindings._thread()
        consumer = self._consumer(owner, handle)
        if self._state != "finished":
            raise PlanError("not_ready", "Export begins only after race completion")
        # Each consumer gets its own plain result tree. Keep uint64 counters as
        # Python integers; the public bounded copier only permits signed int64.
        data = deepcopy(self._data) if self._data is not None else dict(rows=[], windows=[], failures=[])
        # Calls-only affects the result projection, not native instrumentation.
        width = 8 if "elapsed" in self._plan.metrics else 6
        records = [json.loads(t.record) for t in self._plan.targets]
        timings = []
        for window, function, *counters in data.pop("rows"):
            # Native function indices were fixed by the sorted plan at start;
            # consult that plan, never today's mutable catalogue/registry.
            target = self._plan.targets[function]
            timings.append((window, target.function_id, target.generation, *counters[:width]))
        data["timings"] = timings
        data["fields"] = FIELDS[:width]
        data["functions"], data["missing"] = records, [json.loads(row) for row in self._plan.missing]
        data["failures"] = list(dict.fromkeys(data["failures"] + self._failures))
        # Successful cleanup/export does not erase collection loss, declared
        # partial coverage or cancellation of the race itself.
        data["incomplete"] = bool(data["failures"] or self._plan.missing or self._outcome == "cancelled")
        data.update(
            capture=self.capture_id, race=self.race,
            process=self.bindings.process, runtime=self.bindings.runtime,
            context=self.context, adapter=self.adapter, mode=self.mode,
            outcome=self._outcome, metrics=list(self._plan.metrics),
            coverage=self._plan.coverage, delivery=consumer[1],
            losses=deepcopy(self._losses),
        )
        return data
