# SPDX-License-Identifier: MPL-2.0
"""Start the supplied game command and track its Frida sessions until the game exits."""

from contextlib import contextmanager
import json
from functools import partial
import math
import os
from pathlib import Path
import queue
import threading
import time

from launcher.process_exit import ProcessExit

from launcher.diagnostics.console_shutdown import ConsoleShutdown
from launcher.diagnostics import activity_scope, bind_reporter, exception_from_record, handle_error, log_ownership, write_log, reporter, sync_log
from launcher.diagnostics.activity_tracker import ActivityTracker


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
    with log_ownership(path, error_type=LaunchError):
        yield


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
        self.activity = getattr(submit_log, "launch_activity", None) or ActivityTracker()
        self.restart_grace = restart_grace
        self.clock = clock
        self.events = queue.Queue()
        self.sessions = {}
        self.exit_waits = {}
        self.bridges = {}
        self.bridge_factory = bridge_factory
        # A trusted factory creates external mod scopes; Frida callbacks never execute mod code.
        self.mods_factory = mods_factory
        self.pack_mods = {}
        self.parents = set()
        self.held = set()
        self.resumed = set()
        self.subscriptions = []
        self.idle_since = None
        self._restart_timer = self._restart_token = None
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
        existing = self.sessions.get(pid)
        if existing is not None:
            if not existing.is_detached:
                return
            # A new process may reuse this PID before the old detach event is handled.
            # Remove only the detached handle; its delayed event cannot remove the new session.
            self._drain_activity(pid)
            self._discard_session(pid)
        if not self._matches(pid):
            raise LaunchError(f"Cannot verify the game executable for pid={pid}.")
        self.activity.attach(pid)
        if self.shutdown is not None:
            self.shutdown.track(pid)
        # Pin before attachment and verify it is still alive afterwards. This
        # prevents a reused PID from borrowing the old process's exit wait.
        waiter = ProcessExit(pid, self.game, lambda error: self.events.put(
            ("exit", (pid, session, waiter, error))))
        try:
            session = self.device.attach(pid)
        except BaseException:
            waiter.close()
            raise
        self.parents.add(pid)
        # Store the session before enabling gating so partial setup is still cleaned up.
        self.sessions[pid] = session
        self.exit_waits[pid] = waiter
        session.on(
            "detached",
            lambda reason, crash=None: self.events.put(
                ("detached", (pid, session, reason))
            ),
        )
        waiter.start()
        session.enable_child_gating()
        if self.bridge_factory is not None:
            # Script messages are queued just like process events; this thread owns session state.
            bridge = self.bridge_factory(
                session,
                pid,
                lambda message: self.events.put(("bridge", (pid, session, message))),
                **({"bootstrap": False} if not bootstrap else {}),
            )
            self.bridges[pid] = bridge
            bridge.start()
        self._cancel_restart()
        self.submit_log(
            f"ATTACHED pid={pid}; child tracking active; awaiting manager bootstrap"
        )

    def _resume(self, pid):
        # One child notification may also appear in the pending-child list during shutdown.
        if pid in self.resumed:
            return
        try:
            self.device.resume(pid)
        except Exception as error:
            # A short-lived process may disappear before it can be resumed.
            if self._processes([pid]):
                raise LaunchError(f"Could not resume pid={pid}: {error}") from error
        self.resumed.add(pid)
        self.held.discard(pid)

    def _close_mods(self, pid):
        """Release a process's external sandboxes before discarding or replacing its session."""
        scope = self.pack_mods.pop(pid, None)
        if scope is not None:
            try:
                scope.close()
            except Exception as error:
                self.cleanup_failed = True
                self.submit_log("CLEANUP ERROR", level="ERROR", error=error, pid=pid)

    def _discard_session(self, pid):
        """Release owned resources before forgetting a retired session."""
        self._close_mods(pid)
        self._discard_bridge(pid)
        waiter = self.exit_waits.get(pid)
        if waiter is not None:
            waiter.close()
            self.exit_waits.pop(pid)
        self.sessions.pop(pid, None)

    def _drain_activity(self, pid):
        bridge, session = self.bridges.get(pid), self.sessions.get(pid)
        if session is not None and callable(drain := getattr(bridge, "drain_activity", None)):
            journal_error = None
            try:
                records = drain()
            except Exception as error:
                # A torn append must not hide a complete socket-only error.
                journal_error = error
                fallback = getattr(bridge, "drain_fallback", None)
                records = fallback() if callable(fallback) else []
            changes, protocol_errors = [], []
            def invalid_record(record, detail):
                journal = getattr(bridge, "journal", None)
                if journal is not None:
                    journal.retained = True
                protocol_errors.append(LaunchError(detail + ": " + json.dumps(record, ensure_ascii=True)))
            for record in records:
                if type(record) is not dict:
                    invalid_record(record, "Expected a native journal record")
                    continue
                event = record.get("event", "native-activity")
                if event not in ("native-activity", "diagnostic", "readiness", "pack", "menu", "settings", "themes", "career-preload", "native-service"):
                    invalid_record(record, "Unknown native journal event")
                    continue
                sequence = record.get("event_sequence")
                if sequence is not None and (type(sequence) is not int or sequence <= 0):
                    invalid_record(record, "Invalid native change sequence")
                    continue
                if (event in ("readiness", "menu", "settings", "themes", "career-preload") and type(record.get("status")) is not dict
                        or event == "pack" and type(record.get("pack")) is not dict):
                    invalid_record(record, "Invalid native change data")
                    continue
                if event in ("native-activity", "diagnostic") and (
                        type(record.get("detail")) is not str
                        or "fatal" in record and type(record["fatal"]) is not bool
                        or "reason" in record and type(record["reason"]) is not str):
                    invalid_record(record, "Invalid native diagnostic data")
                    continue
                message = {"type": "send", "payload": {**record, "event": event}}
                if event in ("native-activity", "diagnostic"):
                    self._event("bridge", (pid, session, message))
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
            # Preserve complete errors before a change triggers mod preparation
            # or a main-thread request, either of which may itself fail or wait.
            for message in changes:
                if not session.is_detached and not getattr(bridge, "exited", False):
                    self._event("bridge", (pid, session, message))
            if journal_error is not None:
                raise journal_error

    def _discard_bridge(self, pid):
        bridge = self.bridges.get(pid)
        finish = getattr(bridge, "finish_reporting", None)
        if callable(finish):
            finish()
            # The receiver may have queued a final fallback while stopping.
            # Consume it while this exact session still owns the process.
            self._drain_activity(pid)
        self.bridges.pop(pid, None)
        journal = getattr(bridge, "journal", None)
        if journal is not None:
            journal.remove()

    @staticmethod
    def build_native_error(record):
        error = exception_from_record("Game operation failed: " + record.get("reason", record.get("detail", "Unknown native error")), record, LaunchError)
        error.launcher_activity = record["activity"]
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
        finally:
            # An attach/setup failure must not leave the initial process suspended.
            self._resume(pid)
        self.submit_log("RUNNING; tracking game children and pack-switch replacements")

    def _child(self, child):
        # Other Frida clients may share the device; touch only children of our sessions.
        if child.parent_pid not in self.parents:
            return
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
                    if (bridge := self.bridges.get(child.pid)) is not None:
                        bridge.exited = True
                    self._drain_activity(child.pid)
                    previous = self.sessions.get(child.pid)
                    self._discard_session(child.pid)
                    self.activity.retire(child.pid)
                    self.resumed.discard(child.pid)
                    if previous is not None and not previous.is_detached:
                        previous.detach()
                if matches:
                    self._attach(
                        child.pid, bootstrap=getattr(child, "origin", None) != "fork"
                    )
                else:
                    self.submit_log(f"HELPER pid={child.pid}; resumed without attachment")
        finally:
            # Always release a gated child, including on verification/attach failure or shutdown.
            self._resume(child.pid)

    def _event(self, kind, value):
        if kind in ("bridge", "detached", "exit"):
            pid, session = value[:2]
            if self.sessions.get(pid) is session:
                event = value[2].get("payload", {}).get("event") if kind == "bridge" else None
                with self.activity.focus(pid, native=event != "pack"):
                    return self._process_event(kind, value)
        return self._process_event(kind, value)

    def _has_exited(self, pid):
        waiter = self.exit_waits.get(pid)
        return waiter is not None and waiter.has_exited()

    def _process_event(self, kind, value):
        # Each detached event carries its session identity; an old event cannot remove a new session.
        if kind == "child":
            self._child(value)
        elif kind == "mods-ready":
            pid, session = value
            if (self.sessions.get(pid) is session and pid in self.pack_mods
                    and not self.stopping and not self._has_exited(pid)):
                with self.activity.focus(pid):
                    self.pack_mods[pid].poll()
        elif kind == "exit":
            pid, session, waiter, error = value
            if self.sessions.get(pid) is not session or self.exit_waits.get(pid) is not waiter:
                return
            if error is not None:
                raise LaunchError(f"Process exit wait failed for pid={pid}: {error}") from error
            # Detach before closing external resources: no live native UI needs
            # restoration after the pinned process has actually exited.
            if (bridge := self.bridges.get(pid)) is not None:
                bridge.exited = True
            if not session.is_detached:
                try:
                    session.detach()
                except Exception:
                    pass  # The pinned OS handle has already certified exit.
            self._process_event("detached", (pid, session, "process-terminated"))
            self.submit_log(f"EXITED pid={pid}")
        elif kind == "restart-expired":
            if value is not self._restart_token:
                return
        elif kind == "shutdown":
            return  # The close owner already set its request flag.
        elif kind == "detached":
            pid, session, reason = value
            if self.sessions.get(pid) is session:
                if reason in ("process-terminated", "process-replaced") and (bridge := self.bridges.get(pid)) is not None:
                    bridge.exited = True
                self._drain_activity(pid)
                self.submit_log(f"DETACHED pid={pid}: {reason}")
                if not self.stopping and reason not in (
                    "process-terminated",
                    "process-replaced",
                ):
                    # Retain the handle for cleanup when tracking fails while the process is alive.
                    raise LaunchError(
                        f"Game tracking ended unexpectedly for pid={pid}: {reason}"
                    )
                self._discard_session(pid)
                self.activity.retire(pid)
        elif kind == "bridge":
            pid, session, message = value
            if self.sessions.get(pid) is not session:
                return  # A delayed message cannot describe a replacement's manager.
            payload = message.get("payload", {})
            event = payload.get("event")
            bridge = self.bridges.get(pid)
            # Socket data and completion events may precede the exit callback.
            # Check the pinned owner, never a possible new occupant of its PID.
            if self._has_exited(pid):
                if bridge is not None:
                    bridge.exited = True
                if event in ("native-service", "mods-ready", "manager", "pack", "menu", "settings",
                             "themes", "career-preload", "readiness"):
                    return
            if self.stopping and event not in ("native-activity", "diagnostic", "journal-ready", "reporting-closed"):
                return
            if message.get("type") == "error":
                raise exception_from_record(
                    f"Hook bridge failed for pid={pid}: {message.get('description', 'script error')}",
                    {"traceback": message.get("stack")}, LaunchError)
            sequence = payload.get("event_sequence")
            if sequence is not None:
                if type(sequence) is not int or sequence <= 0:
                    journal = getattr(bridge, "journal", None)
                    if journal is not None:
                        journal.retained = True
                    raise LaunchError("Invalid native change sequence: " + json.dumps(payload, ensure_ascii=True))
                previous = bridge.event_sequences.get(event, 0)
                if sequence <= previous:
                    return
                bridge.event_sequences[event] = sequence
            if event == "native-service":
                bridge.request("changes")
                return
            if event == "mods-ready" and pid in self.pack_mods:
                self.pack_mods[pid].poll()
                return
            if event in ("journal-ready", "reporting-closed"):
                if event == "reporting-closed":
                    journal = getattr(self.bridges.get(pid), "journal", None)
                    if journal is not None:
                        journal.retained = True
                self._drain_activity(pid)
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
                # The session check above also rejects delayed records from replaced processes.
                pack = payload.get("pack", {})
                self.submit_log(
                    f"PACK DISCOVERY pid={pid}: {json.dumps(pack, allow_nan=False)}"
                )
                if self.mods_factory is not None:
                    if pid not in self.pack_mods:
                        self.pack_mods[pid] = self.mods_factory(
                            partial(self.submit_log, pid=pid, component="mods"),
                            self.bridges.get(pid),
                        )
                        scope = self.pack_mods[pid]
                        if callable(set_wake := getattr(scope, "set_wake", None)):
                            bridge = self.bridges.get(pid)
                            set_wake(bridge.wake if bridge is not None else
                                lambda: self.events.put(("mods-ready", (pid, session))))
                    self.pack_mods[pid].load(pack)
            elif event == "menu" and pid in self.pack_mods:
                self.pack_mods[pid].menu_status(payload.get("status", {}))
            elif event == "settings" and pid in self.pack_mods:
                self.pack_mods[pid].settings_status(payload.get("status", {}))
            elif event == "themes" and pid in self.pack_mods:
                self.pack_mods[pid].themes_status(payload.get("status", {}))
            elif event == "career-preload" and pid in self.pack_mods:
                self.pack_mods[pid].career_preload(payload.get("status", {}))
            elif event == "readiness":
                status = payload.get("status", {})
                self.activity.observe(pid, status.get("activity"))
                if pid in self.pack_mods:
                    self.pack_mods[pid].readiness_status(status)
            elif event in ("waiting", "transport", "compatibility", "diagnostic"):
                # The verified session owns the PID, regardless of report contents.
                self.submit_log({**payload, "activity": self.activity.native_context(pid, payload.get("activity")),
                             "component": payload.get("component", "bridge"), "pid": pid,
                             "level": payload.get("level", "DEBUG" if event == "waiting" else "WARNING")})
        elif kind == "lost" and not self.stopping:
            raise LaunchError("The local Frida device was lost.")

    def poll(self, timeout=0):
        """Process queued work; timeout=None blocks until an event or owned deadline."""
        try:
            self._event(*self.events.get(timeout=timeout))
        except queue.Empty:
            pass
        while not self.events.empty():
            self._event(*self.events.get_nowait())
        if self.native_failure is not None and not self.stopping:
            failure, self.native_failure = self.native_failure, None
            raise self.build_native_error(failure)
        now = self.clock()
        if self.sessions or self.held:
            self._cancel_restart()
            return False
        # Wait for queued/gated replacements instead of treating the first parent exit as game exit.
        if self.idle_since is None:
            self.idle_since = now
            token = self._restart_token = object()
            self._restart_timer = threading.Timer(self.restart_grace,
                lambda: self.events.put(("restart-expired", token)))
            self._restart_timer.daemon = True
            self._restart_timer.start()
            self.submit_log(
                f"WAITING FOR RESTART; no game processes remain; grace={self.restart_grace:g}s"
            )
        return now - self.idle_since >= self.restart_grace

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
        def attempt(action, session=None, pid=None):
            try:
                action()
            except Exception as error:
                # Exiting processes can detach between the check and the cleanup call.
                if session is not None:
                    gone = session.is_detached
                    if not gone and pid is not None:
                        try:
                            # Frida's detached flag can lag actual process exit.
                            # No native restoration is needed once the pinned owner exits.
                            gone = self._has_exited(pid)
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
            while not self.events.empty():
                attempt(lambda: self._event(*self.events.get_nowait()))

        attempt(release_pending)
        for pid, session in list(self.sessions.items()):
            # Revoke external mod grants while the live game's bridge is still available.
            self._close_mods(pid)
            bridge = self.bridges.get(pid)
            if bridge is not None:
                # Restore Python wrappers before detaching the script/session from a live process.
                attempt(bridge.close, session, pid)
                attempt(lambda pid=pid: self._drain_activity(pid))
                attempt(lambda pid=pid: self._discard_bridge(pid))
            if not session.is_detached:
                attempt(session.disable_child_gating, session, pid)
            if not session.is_detached:
                attempt(session.detach, session, pid)
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
        for pid in self.sessions:
            self.activity.retire(pid)
        self.sessions.clear()
        self.bridges.clear()
        for waiter in self.exit_waits.values():
            attempt(waiter.close)
        self.exit_waits.clear()
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
):
    """Run one controller and return 0 on normal exit, 1 on failure or 130 on interruption."""
    if logger is None:
        # Direct controller callers own the same log session as normal entry.
        # Entry already holds ownership while checking dependencies and supplies
        # its logger, so it must not acquire the lock or truncate the log again.
        outcome = {}
        with controller_lock(lock_path), reporter(log_path, outcome=outcome) as logger:
            code = run_launcher(frida, command, lock_path, log_path, restart_grace=restart_grace,
                                bridge_factory=bridge_factory, mods_factory=mods_factory, logger=logger)
        return 1 if outcome["log_error"] is not None else code

    outcome = logger.launch_outcome
    controller, shutdown, code, stage = None, None, 0, "controller_setup"
    try:
        with ConsoleShutdown(command[0], logger) as shutdown:
            controller = Controller(frida.get_local_device(), command, logger,
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
