# SPDX-License-Identifier: MPL-2.0
"""Start the supplied game command and track its Frida sessions until the game exits."""

from contextlib import contextmanager, nullcontext
import json
import math
import os
from pathlib import Path
import queue
import threading
import time

from launcher.process_exit import ProcessExit
from launcher.restart_evidence import replacement_decision
from launcher.native_record import validate_native_record
from launcher.tracked_process import TrackedProcess
from launcher.process_ended import ProcessEnded

from launcher.diagnostics.console_shutdown import ConsoleShutdown
from launcher.diagnostics import activity_scope, bind_reporter, exception_from_record, handle_error, log_ownership, write_log, reporter, startup_step, sync_log
from launcher.diagnostics.activity_tracker import ActivityTracker
from launcher.diagnostics.reporting_queue import is_terminal_message


class LaunchError(RuntimeError):
    """Raised when the game command or its process tracking cannot be used safely."""


def canonical(path):
    # Compare complete resolved paths; a matching filename alone is not enough.
    return os.path.normcase(str(Path(path).resolve()))


def validate_command(command, expected):
    """Return the supplied arguments after checking the executable against expected."""
    # Steam supplies an argument list after --; never turn it into a shell command.
    if not command or any(not isinstance(arg, str) or "\0" in arg for arg in command):
        raise LaunchError("Supply Steam's original executable and arguments after --.")
    executable = Path(command[0])
    # Do not search PATH or interpret Steam URLs, shell commands or another program.
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or canonical(executable) != canonical(expected)
    ):
        raise LaunchError(
            f"The Steam command must start the installed game: {expected}"
        )
    return [str(executable.resolve()), *command[1:]]


@contextmanager
def controller_lock(path):
    """Hold an OS file lock until this controller finishes, including error cleanup."""
    with log_ownership(path, error_type=LaunchError) as ownership:
        yield ownership


class Controller:
    """Serialize Frida callbacks and keep tracking across the game's process replacements."""

    def __init__(
        self,
        device,
        command,
        submit_log,
        *,
        restart_grace=2.0,
        clock=time.monotonic,
        bridge_factory=None,
        mods_factory=None,
        shutdown=None,
    ):
        # device is Frida's local device; command is validated argv. submit_log is
        # the standard logger or a text observer supplied by trusted callers.
        # bridge_factory optionally supplies our trusted script for each verified session.
        # clock and restart_grace make the restart-gap policy testable without sleeps.
        if not math.isfinite(restart_grace) or restart_grace <= 0:
            raise LaunchError(
                "Restart grace must be a finite number greater than zero."
            )
        self.device = device
        self.command = list(command)
        self.game = canonical(command[0])
        self.submit_log = bind_reporter(submit_log, component="controller")
        self.activity = self.submit_log.owner.activity if self.submit_log.owner is not None else ActivityTracker()
        self.restart_grace = restart_grace
        self.clock = clock
        self.events = queue.Queue()
        self.lifecycle_events = queue.Queue()
        self.processes = {}
        self.bridge_factory = bridge_factory
        # A trusted factory creates external mod scopes; Frida callbacks never execute mod code.
        self.mods_factory = mods_factory
        self.parents = set()
        self.held = set()
        self.resumed = set()
        self.subscriptions = []
        self.idle_since = None
        self._restart_timer = self._restart_token = None
        self.replacement = ("unknown", "no final native reporting evidence")
        self.stopping = False
        self.cleanup_failed = False
        self.native_failure = None
        self.shutdown = shutdown
        if shutdown is not None:
            shutdown.wake = lambda: self.events.put(("shutdown", None))

    def _subscribe(self, signal, callback):
        # Retain the exact callback so close() can remove only our subscription.
        self.device.on(signal, callback)
        self.subscriptions.append((signal, callback))

    def _processes(self, pids=None):
        # Frida supplies actual executable paths; this uses no process-name matching.
        options = {"scope": "metadata"}
        if pids is not None:
            options["pids"] = list(pids)
        return self.device.enumerate_processes(**options)

    def _matches(self, pid):
        # Fail closed if metadata is missing or the executable cannot be identified.
        processes = self._processes([pid])
        return any(
            process.pid == pid
            and process.parameters.get("path")
            and canonical(process.parameters["path"]) == self.game
            for process in processes
        )

    def _attach(self, pid, *, bootstrap=True):
        # Attach only to verified game executables from our spawned/gated process chain.
        # Fork transitions need child gating, but inherit Python state until exec replaces it.
        existing = self.processes.get(pid)
        if existing is not None:
            if not existing.session.is_detached:
                return
            # A new process may reuse this PID before the old detach event is handled.
            # Retire that owner; its delayed events cannot remove the replacement.
            self._discard_session(existing)
        if not self._matches(pid):
            raise LaunchError(f"Cannot verify the game executable for pid={pid}.")
        self.activity.attach(pid)
        if self.shutdown is not None:
            self.shutdown.track(pid)
        # Pin before attachment and verify it is still alive afterwards. This
        # prevents a reused PID from borrowing the old process's exit wait.
        owner = TrackedProcess(pid)
        cancellation = owner.cancellation
        waiter = owner.exit_wait = ProcessExit(pid, self.game, lambda error: self._queue_lifecycle(
            "exit", (owner, error)))
        try:
            with startup_step(self.submit_log, "Frida process attachment", pid=pid):
                session = owner.session = self.device.attach(pid)
        except BaseException:
            waiter.close()
            raise
        self.parents.add(pid)
        # Store the owner before enabling gating so partial setup is still cleaned up.
        self.processes[pid] = owner
        session.on(
            "detached",
            lambda reason, crash=None: self._queue_lifecycle("detached", (owner, reason)),
        )
        waiter.start()
        cancellation.check()
        session.enable_child_gating()
        cancellation.check()
        if self.bridge_factory is not None:
            # Script messages are queued just like process events; this thread owns session state.
            with startup_step(self.submit_log, "bridge configuration and script creation", pid=pid):
                bridge = self.bridge_factory(
                    session,
                    pid,
                    lambda message: self._queue_bridge(owner, message),
                    **({"bootstrap": False} if not bootstrap else {}),
                )
            owner.bridge = bridge
            bridge.cancellation = cancellation
            cancellation.check()
            with startup_step(self.submit_log, "reporting setup and bridge script loading", pid=pid):
                bridge.start()
        self._cancel_restart()
        self.replacement = ("unknown", "no final native reporting evidence")
        self.submit_log(
            f"ATTACHED pid={pid}; child tracking active; awaiting manager bootstrap"
        )

    def _resume(self, pid):
        # One child notification may also appear in the pending-child list during shutdown.
        if pid in self.resumed:
            return
        try:
            with startup_step(self.submit_log, "game process resume", pid=pid):
                self.device.resume(pid)
        except Exception as error:
            # A short-lived process may disappear before it can be resumed.
            if self._processes([pid]):
                raise LaunchError(f"Could not resume pid={pid}: {error}") from error
        self.resumed.add(pid)
        self.held.discard(pid)

    def _close_mods(self, owner):
        """Release a process's external sandboxes before discarding or replacing its session."""
        scope, owner.mods = owner.mods, None
        if scope is not None:
            try:
                scope.close()
            except Exception as error:
                self.cleanup_failed = True
                self.submit_log("CLEANUP ERROR", level="ERROR", error=error, pid=owner.pid)

    def _discard_session(self, owner):
        """Finish delivery and consume final evidence before releasing this owner."""
        pid, session, bridge = owner.pid, owner.session, owner.bridge
        if self.processes.get(pid) is not owner:
            return
        self._close_mods(owner)
        journal = getattr(bridge, "journal", None)
        errors = []
        def finish(action):
            try:
                action()
            except Exception as error:
                errors.append(error)
                if journal is not None:
                    journal.retained = True
        close_reporting = getattr(bridge, "finish_reporting", None)
        if callable(close_reporting):
            finish(close_reporting)
        # Stop the remaining Frida producer before sealing its evidence inbox.
        # Keep ownership until detach finishes, even if final reporting failed.
        def detach():
            if not session.is_detached:
                try:
                    session.detach()
                except Exception:
                    if not getattr(bridge, "exited", False) and not owner.has_exited():
                        raise
        finish(detach)
        # Read the receiver's final fallback and the durable file while this
        # exact process still owns their records, even if closing delivery failed.
        finish(lambda: self._drain_activity(owner))
        for message in owner.cancellation.finish_evidence():
            # Its source was just drained; replaying a journal wake adds no
            # evidence. Other delivered errors must still be consumed.
            if message.get("payload", {}).get("event") != "journal-ready":
                finish(lambda message=message: self._event("bridge", (owner, message)))
        if journal is not None:
            finish(journal.remove)
        finish(owner.exit_wait.close)
        self.replacement = replacement_decision(bridge)
        self.submit_log("GAME PROCESS ENDED; " + self.replacement[1], level="DEBUG", pid=pid)
        self.processes.pop(pid)
        self.activity.retire(pid)
        for error in errors:
            self.submit_log("FINAL PROCESS EVIDENCE ERROR", level="ERROR", error=error, pid=pid)
        if errors:
            # A delivery/cleanup error cannot replace a complete native fatal.
            raise next((error for error in errors if getattr(error, "launcher_native_failure", False)), errors[0])

    def _queue_lifecycle(self, kind, value):
        """Callbacks only cancel that owner, queue priority work and wake the loop."""
        if not value[0].cancellation.cancel():
            return  # Duplicate OS/Frida callbacks cannot add stale wake-ups.
        self.lifecycle_events.put((kind, value))
        self.events.put(("lifecycle-ready", None))

    def _queue_bridge(self, owner, message):
        """Keep already-delivered diagnostics ahead of session retirement."""
        if is_terminal_message(message):
            if not owner.cancellation.capture(message):
                return
            self.lifecycle_events.put(("bridge-evidence", (owner, message)))
            self.events.put(("lifecycle-ready", None))
        else:
            self.events.put(("bridge", (owner, message)))

    def _drain_activity(self, owner):
        pid, session, bridge = owner.pid, owner.session, owner.bridge
        if callable(drain := getattr(bridge, "drain_activity", None)):
            journal_error = None
            try:
                records = drain()
            except Exception as error:
                # A torn append must not hide a complete socket-only error.
                journal_error = error
                fallback = getattr(bridge, "drain_fallback", None)
                records = getattr(error, "decoded_records", []) + (fallback() if callable(fallback) else [])
            changes, restart_evidence, protocol_errors = [], [], []
            def invalid_record(record, detail):
                journal = getattr(bridge, "journal", None)
                if journal is not None:
                    journal.retained = True
                protocol_errors.append(LaunchError(detail + ": " + json.dumps(record, ensure_ascii=True)))
            for record in records:
                try:
                    record = validate_native_record(record)
                except ValueError as error:
                    invalid_record(record, str(error))
                    continue
                event = record["event"]
                message = {"type": "send", "payload": record}
                if event in ("native-activity", "diagnostic"):
                    self._event("bridge", (owner, message), validated=True)
                elif event in ("restart", "restart-observation"):
                    restart_evidence.append(message)
                else:
                    changes.append(message)
            for error in protocol_errors:
                self.submit_log("NATIVE JOURNAL EVENT FAILED", level="ERROR", error=error, pid=pid)
            if journal_error is not None and (protocol_errors or self.native_failure is not None and not self.stopping):
                self.submit_log("NATIVE JOURNAL READ FAILED", level="ERROR", error=journal_error, pid=pid)
            if self.native_failure is not None and not self.stopping:
                failure, self.native_failure = self.native_failure, None
                raise self.build_native_error(failure)
            if protocol_errors:
                raise protocol_errors[0]
            for message in restart_evidence:
                # Unlike UI changes, final restart evidence is useful after exit.
                self._event("bridge", (owner, message), validated=True)
            # Preserve complete errors before a change triggers mod preparation
            # or a main-thread request, either of which may itself fail or wait.
            for message in changes:
                if not session.is_detached and not getattr(bridge, "exited", False):
                    self._event("bridge", (owner, message), validated=True)
            if journal_error is not None:
                raise journal_error

    @staticmethod
    def build_native_error(record):
        error = exception_from_record("Game operation failed: " + record.get("reason", record.get("detail", "Unknown native error")), record, LaunchError)
        error.launcher_activity = record["activity"]
        error.launcher_native_failure = True
        return error

    def start(self):
        """Spawn, attach and enable child tracking before allowing the game to run."""
        if self.shutdown is not None and self.shutdown.requested.is_set():
            return
        # Refuse a second game instance even if the existing one was launched normally.
        if any(
            process.parameters.get("path")
            and canonical(process.parameters["path"]) == self.game
            for process in self._processes()
        ):
            raise LaunchError(
                "The game is already running; close it before starting through this launcher."
            )
        # Native callbacks only queue events; the controller thread owns all session changes.
        self._subscribe("child-added", lambda child: self.events.put(("child", child)))
        self._subscribe("lost", lambda: self.events.put(("lost", None)))
        # Enumeration/setup can outlast a console-close event. Do not start a
        # fresh game after the user has already asked this controller to exit.
        if self.shutdown is not None and self.shutdown.requested.is_set():
            return
        with startup_step(self.submit_log, "game process spawn"):
            pid = self.device.spawn(
                self.command,
                envp=os.environ.copy(),
                cwd=str(Path(self.command[0]).parent),
                stdio="inherit",
            )
        self.held.add(pid)
        self.submit_log(f"SPAWNED pid={pid}")
        try:
            self._attach(pid)
        except ProcessEnded:
            pass  # The queued owned exit still performs final recovery and cleanup.
        finally:
            # An attach/setup failure must not leave the initial process suspended.
            self._resume(pid)
        self.submit_log("RUNNING; tracking game children and pack-switch replacements")

    def _child(self, child):
        # Other Frida clients may share the device; touch only children of our sessions.
        if child.parent_pid not in self.parents:
            return
        self.submit_log("STARTUP " + json.dumps({"phase": "child process notification", "parent_pid": child.parent_pid,
                        "origin": getattr(child, "origin", None)}), level="DEBUG", pid=child.pid)
        if not self.stopping:
            # A new child can reuse a PID that belonged to an earlier completed process.
            self.resumed.discard(child.pid)
        elif child.pid in self.resumed:
            return
        self.held.add(child.pid)
        try:
            if self.stopping and self.shutdown is not None and self.shutdown.requested.is_set():
                # A pack-switch replacement can already be gated when Close
                # arrives. Track it for window shutdown before releasing it.
                if self._matches(child.pid):
                    self.shutdown.track(child.pid)
            if not self.stopping:
                # Unrelated helpers are resumed without attachment; only the game gets a session.
                matches = self._matches(child.pid)
                if matches:
                    # A journal failure can abort replacement attachment. Own
                    # this verified child before draining its predecessor.
                    if self.shutdown is not None:
                        self.shutdown.track(child.pid)
                # Exec ends the old image even when the new image is a helper.
                # Retire it before draining data: a delayed detach must not let
                # old UI/service records enter a script whose image is gone.
                if getattr(child, "origin", None) == "exec":
                    if (owner := self.processes.get(child.pid)) is not None:
                        if owner.bridge is not None:
                            owner.bridge.exited = True
                        self._discard_session(owner)
                    self.resumed.discard(child.pid)
                if matches:
                    self._attach(
                        child.pid, bootstrap=getattr(child, "origin", None) != "fork"
                    )
                else:
                    self.submit_log(f"HELPER pid={child.pid}; resumed without attachment")
        finally:
            # Always release a gated child, including on verification/attach failure or shutdown.
            self._resume(child.pid)

    def _event(self, kind, value, *, validated=False):
        """Dispatch owned work; journal batches already passed shared shape checks."""
        if kind == "bridge-evidence":
            owner, message = value
            if not owner.cancellation.consume(message):
                return  # Final retirement already consumed this exact delivery.
            kind = "bridge"
        try:
            return self._owned_event(kind, value, validated=validated)
        except ProcessEnded:
            # Only our cancellation checks raise this control signal. Do not
            # turn an interrupted bridge/preparation wait into a fatal mod error.
            return

    def _owned_event(self, kind, value, *, validated=False):
        if kind in ("bridge", "detached", "exit", "mods-ready"):
            owner, detail = value
            if self.processes.get(owner.pid) is not owner:
                return  # A delayed callback cannot borrow a replacement's resources.
            if kind == "bridge" and owner.cancellation.ended.is_set() and not is_terminal_message(detail):
                return
            event = detail.get("payload", {}).get("event") if kind == "bridge" else None
            if kind == "bridge":
                payload = detail.get("payload", {})
                if not validated and detail.get("type") == "send":
                    try:
                        validate_native_record(payload, strict=False)
                    except ValueError as error:
                        journal = getattr(owner.bridge, "journal", None)
                        if journal is not None:
                            journal.retained = True
                        raise LaunchError(str(error) + ": " + json.dumps(payload, ensure_ascii=True)) from error
            with self.activity.focus(owner.pid, native=kind != "mods-ready" and event != "pack"):
                return self._process_event(kind, value)
        return self._process_event(kind, value)

    def _process_event(self, kind, value):
        # Process events have passed the one owner-identity check above.
        if kind == "child":
            self._child(value)
        elif kind == "mods-ready":
            owner, _ = value
            if owner.mods is not None and not self.stopping and not owner.has_exited():
                owner.mods.poll()
        elif kind == "exit":
            owner, error = value
            pid, session, bridge = owner.pid, owner.session, owner.bridge
            if error is not None:
                raise LaunchError(f"Process exit wait failed for pid={pid}: {error}") from error
            # Detach before closing external resources: no live native UI needs
            # restoration after the pinned process has actually exited.
            if bridge is not None:
                bridge.exited = True
            if not session.is_detached:
                try:
                    session.detach()
                except Exception:
                    pass  # The pinned OS handle has already certified exit.
            self._process_event("detached", (owner, "process-terminated"))
            self.submit_log(f"EXITED pid={pid}")
        elif kind == "restart-expired":
            if value is None or value is not self._restart_token:
                return
            # A timer wake may arrive just before the clock reaches its deadline.
            # Mark it consumed so poll can arm the remaining time instead of
            # blocking forever after that one wake has been used.
            self._restart_timer = self._restart_token = None
            self.submit_log(f"REPLACEMENT GRACE WAKE; elapsed={self.clock() - self.idle_since:.6f}s", level="DEBUG")
        elif kind in ("shutdown", "lifecycle-ready"):
            return  # The close owner already set its request flag.
        elif kind == "detached":
            owner, reason = value
            pid, bridge = owner.pid, owner.bridge
            if reason in ("process-terminated", "process-replaced") and bridge is not None:
                bridge.exited = True
            self.submit_log(f"DETACHED pid={pid}: {reason}")
            if not self.stopping and reason not in (
                "process-terminated",
                "process-replaced",
            ):
                # Retain the handle for cleanup when tracking fails while the process is alive.
                # A complete native fatal already in its journal takes
                # precedence over this later transport failure.
                self._drain_activity(owner)
                raise LaunchError(
                    f"Game tracking ended unexpectedly for pid={pid}: {reason}"
                )
            self._discard_session(owner)
        elif kind == "bridge":
            owner, message = value
            pid, session, bridge = owner.pid, owner.session, owner.bridge
            payload = message.get("payload", {})
            event = payload.get("event")
            # Socket data and completion events may precede the exit callback.
            # Check the pinned owner, never a possible new occupant of its PID.
            if owner.has_exited():
                if bridge is not None:
                    bridge.exited = True
                if event in ("native-service", "mods-ready", "manager", "pack", "menu", "settings",
                             "themes", "career-preload", "readiness"):
                    return
            if self.stopping and not is_terminal_message(message):
                return
            if message.get("type") == "error":
                raise exception_from_record(
                    f"Hook bridge failed for pid={pid}: {message.get('description', 'script error')}",
                    {"traceback": message.get("stack")}, LaunchError)
            sequence = payload.get("event_sequence")
            if sequence is not None:
                previous = bridge.event_sequences.get(event, 0)
                if sequence <= previous:
                    return
                bridge.event_sequences[event] = sequence
            if event in ("restart", "restart-observation"):
                # Consume final evidence even after detach. Owner identity and
                # sequences above reject duplicates and messages from old owners.
                attribute = "restart_attempt" if event == "restart" else "restart_observation"
                status, previous = payload["status"], getattr(bridge, attribute)
                if event != "restart" or previous is None or status["attempt"] >= previous["attempt"]:
                    setattr(bridge, attribute, status)
                self.submit_log("NATIVE " + event.upper() + ": " + json.dumps(payload["status"], ensure_ascii=True),
                                level="DEBUG" if event == "restart-observation" else "INFO", pid=pid)
                return
            if event == "native-service":
                bridge.request("changes")
                return
            if event == "mods-ready" and owner.mods is not None:
                owner.mods.poll()
                return
            if event in ("journal-ready", "reporting-closed"):
                if event == "reporting-closed":
                    journal = getattr(bridge, "journal", None)
                    if journal is not None:
                        journal.retained = True
                self._drain_activity(owner)
                if event == "reporting-closed":
                    detail = payload.get("detail", "Native reporting stopped unexpectedly")
                    if not self.stopping and not getattr(bridge, "exited", False) and not session.is_detached:
                        raise LaunchError("Native reporting failed for pid=" + str(pid) + ": " + detail)
                    self.submit_log("NATIVE REPORTING CLOSED: " + detail, level="WARNING", pid=pid)
                return
            if event == "native-activity":
                activity = self.activity.native_context(pid, payload.get("activity"))
                if payload.get("transition"):
                    self.activity.observe(pid, payload.get("activity"))
                record = {**payload, "activity": activity, "pid": pid}
                self.submit_log(record)
                if payload.get("fatal"):
                    if self.native_failure is None:
                        self.native_failure = record
                return
            if event == "error":
                error = exception_from_record(
                    f"Hook manager failed for pid={pid}: {payload.get('detail', 'unknown error')}",
                    payload, LaunchError)
                activity = self.activity.native_context(pid, payload.get("activity"))
                if activity is not None:
                    error.launcher_activity = activity
                raise error
            if event == "manager":
                status = payload.get("status", {})
                if status.get("state") != "ready":
                    raise LaunchError(f"Hook manager is not ready for pid={pid}")
                self.submit_log(f"HOOK MANAGER READY pid={pid}; awaiting startup pack")
            elif event == "pack":
                # Quote paths and IDs as JSON so a diagnostic remains a single log record.
                # The owner check above rejects delayed records from replaced processes.
                pack = payload.get("pack", {})
                self.submit_log(
                    f"PACK DISCOVERY pid={pid}: {json.dumps(pack, allow_nan=False)}"
                )
                if self.mods_factory is not None:
                    if owner.mods is None:
                        owner.mods = self.mods_factory(
                            self.submit_log.bind_log(pid=pid, component="mods"),
                            bridge,
                        )
                        scope = owner.mods
                        if callable(set_wake := getattr(scope, "set_wake", None)):
                            set_wake(bridge.wake if bridge is not None else
                                lambda: self.events.put(("mods-ready", (owner, None))))
                    owner.mods.load(pack)
            elif event == "menu" and owner.mods is not None:
                owner.mods.menu_status(payload.get("status", {}))
            elif event == "settings" and owner.mods is not None:
                owner.mods.settings_status(payload.get("status", {}))
            elif event == "themes" and owner.mods is not None:
                owner.mods.themes_status(payload.get("status", {}))
            elif event == "career-preload" and owner.mods is not None:
                owner.mods.career_preload(payload.get("status", {}))
            elif event == "readiness":
                status = payload.get("status", {})
                self.activity.observe(pid, status.get("activity"))
                if owner.mods is not None:
                    owner.mods.readiness_status(status)
            elif event in ("waiting", "transport", "compatibility", "diagnostic"):
                # The verified session owns the PID, regardless of report contents.
                self.submit_log({**payload, "activity": self.activity.native_context(pid, payload.get("activity")),
                             "component": payload.get("component", "bridge"), "pid": pid,
                             "level": payload.get("level", "DEBUG" if event == "waiting" else "WARNING")})
        elif kind == "lost" and not self.stopping:
            raise LaunchError("The local Frida device was lost.")

    def poll(self, timeout=0):
        """Process queued work; timeout=None blocks until an event or owned deadline."""
        first = True
        while True:
            # Drain lifecycle work before admitting another routine event. The
            # callback has already woken active waits through its owner's flag.
            while not self.lifecycle_events.empty():
                self._event(*self.lifecycle_events.get_nowait())
            try:
                event = self.events.get(timeout=timeout) if first else self.events.get_nowait()
            except queue.Empty:
                break
            first = False
            while not self.lifecycle_events.empty():
                self._event(*self.lifecycle_events.get_nowait())
            self._event(*event)
        if self.native_failure is not None and not self.stopping:
            failure, self.native_failure = self.native_failure, None
            raise self.build_native_error(failure)
        now = self.clock()
        if self.processes or self.held:
            self._cancel_restart()
            return False
        if self.replacement[0] == "ordinary":
            self._cancel_restart()
            return True
        # Wait for queued/gated replacements instead of treating the first parent exit as game exit.
        starting_wait = self.idle_since is None
        if starting_wait:
            self.idle_since = now
        remaining = self.restart_grace - (now - self.idle_since)
        if remaining <= 0:
            if self.replacement[0] == "restart":
                self.submit_log("RESTART REPLACEMENT NOT OBSERVED; " + self.replacement[1], level="WARNING")
            return True
        if self._restart_timer is None:
            token = self._restart_token = object()
            self._restart_timer = threading.Timer(remaining,
                lambda: self.events.put(("restart-expired", token)))
            self._restart_timer.daemon = True
            self._restart_timer.start()
        if starting_wait:
            # Arm the deadline before console/file output can delay this thread.
            self.submit_log(
                f"NO GAME PROCESSES REMAIN; allowing up to {self.restart_grace:g}s for a possible replacement; {self.replacement[1]}"
            )
        return False

    def _cancel_restart(self):
        if self._restart_timer is not None:
            self._restart_timer.cancel()
        self._restart_timer = self._restart_token = self.idle_since = None

    def close(self):
        """Release owned children, disable child gating and detach every remaining session."""
        self.stopping = True
        self._cancel_restart()
        self.resumed.clear()

        # Keep event subscriptions until gating is disabled and late children have been released.
        def attempt(action, owner=None):
            try:
                action()
            except ProcessEnded:
                pass  # Exit can interrupt a live shutdown request; recovery still runs below.
            except Exception as error:
                # Exiting processes can detach between the check and the cleanup call.
                if owner is not None:
                    gone = owner.session.is_detached or owner.cancellation.ended.is_set()
                    if not gone:
                        try:
                            # Frida's detached flag can lag actual process exit.
                            # No native restoration is needed once the pinned owner exits.
                            gone = owner.has_exited()
                        except Exception:
                            pass  # Unconfirmed exit keeps the original cleanup error.
                    if gone:
                        return
                self.cleanup_failed = True
                self.submit_log("CLEANUP ERROR", level="ERROR", error=error)

        def release_pending():
            for child in self.device.enumerate_pending_children():
                if child.parent_pid in self.parents:
                    self.held.add(child.pid)
                    if self.shutdown is not None and self.shutdown.requested.is_set():
                        # Enumeration may release this child before its queued
                        # notification runs; own verified game handles first.
                        attempt(lambda child=child: self.shutdown.track(child.pid) if self._matches(child.pid) else None)
                    # The pending list confirms it is currently gated, even if its PID was reused.
                    self.resumed.discard(child.pid)
                    attempt(lambda child=child: self._resume(child.pid))
            while not self.lifecycle_events.empty() or not self.events.empty():
                pending = self.lifecycle_events if not self.lifecycle_events.empty() else self.events
                attempt(lambda: self._event(*pending.get_nowait()))

        attempt(release_pending)
        for owner in list(self.processes.values()):
            session, bridge = owner.session, owner.bridge
            # Revoke external mod grants while the live game's bridge is still available.
            self._close_mods(owner)
            if bridge is not None:
                # Restore Python wrappers before detaching the script/session from a live process.
                attempt(getattr(bridge, "close_native", bridge.close), owner)
            if not session.is_detached:
                attempt(session.disable_child_gating, owner)
            attempt(lambda owner=owner: self._discard_session(owner))
        # Reconcile notifications arriving while the sessions were being detached.
        attempt(release_pending)
        for pid in list(self.held):
            attempt(lambda pid=pid: self._resume(pid))
        for signal, callback in self.subscriptions:
            attempt(
                lambda signal=signal, callback=callback: self.device.off(
                    signal, callback
                )
            )
        self.subscriptions.clear()
        for owner in self.processes.values():
            self.activity.retire(owner.pid)
            attempt(owner.exit_wait.close)
        self.processes.clear()
        if self.shutdown is not None:
            self.shutdown.wake = None
        if self.cleanup_failed:
            self.submit_log("CONTROLLER STOPPED; cleanup incomplete; see errors above", level="ERROR")
        else:
            self.submit_log("CONTROLLER STOPPED; cleanup complete")


def run_launcher(
    frida,
    command,
    lock_path,
    log_path,
    *,
    restart_grace=2.0,
    bridge_factory=None,
    mods_factory=None,
    logger=None,
    host_lifetime=None,
):
    """Run one controller and return 0 on normal exit, 1 on failure or 130 on interruption."""
    if logger is None:
        # Direct controller callers own the same log session as normal entry.
        # Entry already holds ownership while checking dependencies and supplies
        # its logger, so it must not acquire the lock or truncate the log again.
        outcome = {}
        with controller_lock(lock_path) as ownership, reporter(log_path, outcome=outcome, ownership=ownership) as logger:
            code = run_launcher(frida, command, lock_path, log_path, restart_grace=restart_grace,
                                bridge_factory=bridge_factory, mods_factory=mods_factory, logger=logger,
                                host_lifetime=host_lifetime)
        return 1 if outcome["log_error"] is not None else code

    outcome = logger.outcome
    controller, shutdown, code, stage = None, None, 0, "controller_setup"
    try:
        with ConsoleShutdown(command[0], logger) as shutdown, \
                (host_lifetime.watch(shutdown.request_from_host) if host_lifetime is not None else nullcontext()):
            with startup_step(logger, "Frida local device acquisition"):
                device = frida.get_local_device()
            controller = Controller(device, command, logger,
                                    restart_grace=restart_grace, bridge_factory=bridge_factory,
                                    mods_factory=mods_factory, shutdown=shutdown)
            try:
                with activity_scope(logger, "game_loading"):
                    controller.start()
                stage = "controller"
                while not shutdown.requested.is_set() and not controller.poll(timeout=None):
                    pass
                if shutdown.requested.is_set():
                    logger.info("CONSOLE CLOSE requested; closing owned game windows")
                    code = 130
                else:
                    logger.info("GAME SESSION ENDED")
            except KeyboardInterrupt:
                shutdown.request()
                logger.info("INTERRUPTED; closing owned game windows")
                code = 130
            except Exception as error:
                handle_error(logger, error, stage=stage, shutdown=shutdown, component="controller")
                code = 1
            finally:
                stage = "cleanup"
                with activity_scope(logger, "shutdown", pid=None):
                    try:
                        controller.close()
                        outcome["cleanup"] = "failed" if controller.cleanup_failed else "complete"
                        if controller.native_failure is not None and outcome["failure"] is None:
                            handle_error(logger, controller.build_native_error(controller.native_failure), stage="controller",
                                         shutdown=shutdown, component="controller")
                            code = 1
                    except BaseException:
                        outcome["cleanup"] = "failed"
                        shutdown.request()
                        raise
                    if controller.cleanup_failed:
                        if outcome["failure"] is None:
                            handle_error(logger, RuntimeError("Controller cleanup did not complete; see errors above"),
                                         stage="cleanup", shutdown=shutdown, component="controller")
                        code = 1
    except KeyboardInterrupt:
        logger.info("INTERRUPTED during controller setup or cleanup")
        code = 130
    except Exception as error:
        if stage == "cleanup":
            outcome["cleanup"] = "failed"
        handle_error(logger, error, message="LAUNCHER SETUP ERROR" if stage == "controller_setup" else "LAUNCHER CLEANUP ERROR",
                     level="CRITICAL", stage=stage, component="controller")
        code = 1
    finally:
        outcome["game_close"] = getattr(shutdown, "result", "not_requested")
        if outcome["game_close"] == "pending":
            outcome["cleanup"] = "failed"
            if outcome["failure"] is None:
                with activity_scope(logger, "shutdown", pid=None):
                    handle_error(logger, RuntimeError("Owned game exit could not be confirmed"), stage="cleanup", component="controller")
            code = 1
        sync_log(logger)
    return code
