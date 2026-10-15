# SPDX-License-Identifier: MPL-2.0
"""Start the supplied game command and track its Frida sessions until the game exits."""

from contextlib import contextmanager
import json
import logging
import math
import os
from pathlib import Path
import queue
import time


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
    # path is inside the launcher's .state folder; reuse the same inode across launches.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        # Windows locks a byte range, so ensure that byte exists before locking it.
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise LaunchError(
                "Another controller is running, or the controller lock is unavailable."
            ) from error
        # Closing the file releases the OS lock; process termination also releases it.
        # Keep the lock file itself, so another opener cannot lock a different replacement file.
        yield


@contextmanager
def reporter(path):
    """Start a fresh log file and yield a logger for this launch's lifecycle messages."""
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.Logger("modLauncher", logging.INFO)
    # The controller already holds its lock, so clearing this file cannot erase another run's log.
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(path, mode="w", encoding="utf-8"),
    ]
    try:
        for handler in handlers:
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            logger.addHandler(handler)
        yield logger
    finally:
        for handler in handlers:
            handler.close()


class Controller:
    """Serialize Frida callbacks and keep tracking across the game's process replacements."""

    def __init__(
        self,
        device,
        command,
        report,
        *,
        restart_grace=2.0,
        clock=time.monotonic,
        bridge_factory=None,
        mods_factory=None,
    ):
        # device is Frida's local device; command is validated argv; report receives one log line.
        # bridge_factory optionally supplies our trusted script for each verified session.
        # clock and restart_grace make the restart-gap policy testable without sleeps.
        if not math.isfinite(restart_grace) or restart_grace <= 0:
            raise LaunchError(
                "Restart grace must be a finite number greater than zero."
            )
        self.device = device
        self.command = list(command)
        self.game = canonical(command[0])
        self.report = report
        self.restart_grace = restart_grace
        self.clock = clock
        self.events = queue.Queue()
        self.sessions = {}
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
        self.next_check = 0.0
        self.stopping = False
        self.cleanup_failed = False

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
            del self.sessions[pid]
            self._close_mods(pid)
            self.bridges.pop(pid, None)
        if not self._matches(pid):
            raise LaunchError(f"Cannot verify the game executable for pid={pid}.")
        session = self.device.attach(pid)
        self.parents.add(pid)
        # Store the session before enabling gating so partial setup is still cleaned up.
        self.sessions[pid] = session
        session.on(
            "detached",
            lambda reason, crash=None: self.events.put(
                ("detached", (pid, session, reason))
            ),
        )
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
        self.idle_since = None
        self.report(
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
                self.report(f"CLEANUP ERROR pid={pid}: {error}")

    def start(self):
        """Spawn, attach and enable child tracking before allowing the game to run."""
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
        pid = self.device.spawn(
            self.command,
            envp=os.environ.copy(),
            cwd=str(Path(self.command[0]).parent),
            stdio="inherit",
        )
        self.held.add(pid)
        self.report(f"SPAWNED pid={pid}")
        try:
            self._attach(pid)
        finally:
            # An attach/setup failure must not leave the initial process suspended.
            self._resume(pid)
        self.report("RUNNING; tracking game children and pack-switch replacements")

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
            if not self.stopping:
                # Unrelated helpers are resumed without attachment; only the game gets a session.
                if self._matches(child.pid):
                    # Unix exec can replace the process image while retaining its PID.
                    if getattr(child, "origin", None) == "exec":
                        previous = self.sessions.pop(child.pid, None)
                        self._close_mods(child.pid)
                        self.bridges.pop(child.pid, None)
                        self.resumed.discard(child.pid)
                        if previous is not None and not previous.is_detached:
                            previous.detach()
                    self._attach(
                        child.pid, bootstrap=getattr(child, "origin", None) != "fork"
                    )
                else:
                    self.report(f"HELPER pid={child.pid}; resumed without attachment")
        finally:
            # Always release a gated child, including on verification/attach failure or shutdown.
            self._resume(child.pid)

    def _event(self, kind, value):
        # Each detached event carries its session identity; an old event cannot remove a new session.
        if kind == "child":
            self._child(value)
        elif kind == "detached":
            pid, session, reason = value
            if self.sessions.get(pid) is session:
                self.report(f"DETACHED pid={pid}: {reason}")
                if not self.stopping and reason not in (
                    "process-terminated",
                    "process-replaced",
                ):
                    # Retain the handle for cleanup when tracking fails while the process is alive.
                    raise LaunchError(
                        f"Game tracking ended unexpectedly for pid={pid}: {reason}"
                    )
                del self.sessions[pid]
                self._close_mods(pid)
                self.bridges.pop(pid, None)
        elif kind == "bridge" and not self.stopping:
            pid, session, message = value
            if self.sessions.get(pid) is not session:
                return  # A delayed message cannot describe a replacement's manager.
            if message.get("type") == "error":
                raise LaunchError(
                    f"Hook bridge failed for pid={pid}: {message.get('description', 'script error')}"
                )
            payload = message.get("payload", {})
            event = payload.get("event")
            if event == "error":
                raise LaunchError(
                    f"Hook manager failed for pid={pid}: {payload.get('detail', 'unknown error')}"
                )
            if event == "manager":
                status = payload.get("status", {})
                if status.get("state") != "ready":
                    raise LaunchError(f"Hook manager is not ready for pid={pid}")
                self.report(f"HOOK MANAGER READY pid={pid}; awaiting startup pack")
            elif event == "pack":
                # Quote paths and IDs as JSON so a diagnostic remains a single log record.
                # The session check above also rejects delayed records from replaced processes.
                pack = payload.get("pack", {})
                self.report(
                    f"PACK DISCOVERY pid={pid}: {json.dumps(pack, allow_nan=False)}"
                )
                if self.mods_factory is not None:
                    if pid not in self.pack_mods:
                        self.pack_mods[pid] = self.mods_factory(
                            lambda line: self.report(f"pid={pid} {line}")
                        )
                    self.pack_mods[pid].load(pack)
            elif event in ("waiting", "transport"):
                self.report(f"HOOK BRIDGE pid={pid}: {payload.get('detail', '')}")
        elif kind == "lost" and not self.stopping:
            raise LaunchError("The local Frida device was lost.")

    def poll(self, timeout=0):
        """Process queued events and return True after all game processes leave the grace window."""
        try:
            self._event(*self.events.get(timeout=timeout))
        except queue.Empty:
            pass
        while not self.events.empty():
            self._event(*self.events.get_nowait())
        now = self.clock()
        # Confirm process exits even if a detached notification is delayed or missing.
        if self.sessions and now >= self.next_check:
            live = {process.pid: process for process in self._processes(self.sessions)}
            for pid, session in list(self.sessions.items()):
                if pid not in live:
                    del self.sessions[pid]
                    self._close_mods(pid)
                    self.bridges.pop(pid, None)
                    # Drop the local handle without relying on a pending callback for shutdown.
                    try:
                        session.detach()
                    except Exception:
                        pass  # The process is gone; the handle may already be detached.
                    self.report(f"EXITED pid={pid}")
                elif (
                    not live[pid].parameters.get("path")
                    or canonical(live[pid].parameters["path"]) != self.game
                ):
                    raise LaunchError(
                        f"Tracked process identity changed for pid={pid}."
                    )
            self.next_check = now + 0.5
        if self.sessions or self.held:
            self.idle_since = None
            return False
        # Wait for queued/gated replacements instead of treating the first parent exit as game exit.
        if self.idle_since is None:
            self.idle_since = now
            self.report(
                f"WAITING FOR RESTART; no game processes remain; grace={self.restart_grace:g}s"
            )
        return now - self.idle_since >= self.restart_grace

    def close(self):
        """Release owned children, disable child gating and detach every remaining session."""
        self.stopping = True
        self.resumed.clear()

        # Keep event subscriptions until gating is disabled and late children have been released.
        def attempt(action, session=None):
            try:
                action()
            except Exception as error:
                # Exiting processes can detach between the check and the cleanup call.
                if session is not None and session.is_detached:
                    return
                self.cleanup_failed = True
                self.report(f"CLEANUP ERROR: {error}")

        def release_pending():
            for child in self.device.enumerate_pending_children():
                if child.parent_pid in self.parents:
                    self.held.add(child.pid)
                    # The pending list confirms it is currently gated, even if its PID was reused.
                    self.resumed.discard(child.pid)
                    attempt(lambda child=child: self._resume(child.pid))
            while not self.events.empty():
                attempt(lambda: self._event(*self.events.get_nowait()))

        attempt(release_pending)
        for pid, session in list(self.sessions.items()):
            # Revoke external mod grants while the live game's bridge is still available.
            self._close_mods(pid)
            bridge = self.bridges.pop(pid, None)
            if bridge is not None:
                # Restore Python wrappers before detaching the script/session from a live process.
                attempt(bridge.close, session)
            if not session.is_detached:
                attempt(session.disable_child_gating, session)
            if not session.is_detached:
                attempt(session.detach, session)
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
        self.sessions.clear()
        self.bridges.clear()
        if self.cleanup_failed:
            self.report("CONTROLLER STOPPED; cleanup incomplete; see errors above")
        else:
            self.report("CONTROLLER STOPPED; cleanup complete")


def run_launcher(
    frida,
    command,
    lock_path,
    log_path,
    *,
    restart_grace=2.0,
    bridge_factory=None,
    mods_factory=None,
):
    """Run one controller and return 0 on normal exit, 1 on failure or 130 on interruption."""
    # Acquire ownership before opening the log: a rejected duplicate must leave it untouched.
    with controller_lock(lock_path):
        with reporter(log_path) as logger:
            try:
                controller = Controller(
                    frida.get_local_device(),
                    command,
                    logger.info,
                    restart_grace=restart_grace,
                    bridge_factory=bridge_factory,
                    mods_factory=mods_factory,
                )
                code = 0
                try:
                    controller.start()
                    while not controller.poll(timeout=0.25):
                        pass
                    logger.info("GAME SESSION ENDED")
                except KeyboardInterrupt:
                    logger.info("INTERRUPTED; releasing tracking")
                    code = 130
                except Exception as error:
                    logger.error("LAUNCHER ERROR: %s", error)
                    code = 1
                finally:
                    controller.close()
                return 1 if controller.cleanup_failed else code
            except KeyboardInterrupt:
                logger.info("INTERRUPTED during controller setup")
                return 130
            except Exception as error:
                logger.error("LAUNCHER SETUP ERROR: %s", error)
                return 1
