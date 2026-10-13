# SPDX-License-Identifier: MPL-2.0
"""Expose the controller's Frida operations through a private native worker process."""

from contextlib import contextmanager
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import subprocess
import sys
import threading
import time
import tomllib
from types import SimpleNamespace

MAX_FRAME = 8 * 1024 * 1024
FRIDA_VERSION = "17.23.3"
_current = threading.local()


class WorkerError(RuntimeError):
    """The worker failed, rejected a request or did not answer in time."""


def target_name():
    """Return the native release name for this interpreter's OS and CPU."""
    machine = platform.machine().lower()
    cpu = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine)
    system = {"win32": "windows", "darwin": "macos", "linux": "linux"}.get(sys.platform)
    if cpu is None or system is None:
        raise WorkerError("Unsupported worker platform or CPU")
    return f"{system}-{cpu}"


def verify_worker(root):
    """Check the prepared worker's version and file hash before executing it."""
    root = Path(root).resolve()
    directory = root / "libs/core_worker" / target_name()
    executable = directory / ("core-worker.exe" if os.name == "nt" else "core-worker")
    manifest = directory / "worker.toml"
    # A pack or link must not redirect the trusted worker outside the launcher.
    for path in (directory, executable, manifest):
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise WorkerError("Worker path escapes the launcher folder")
    try:
        settings = tomllib.loads(manifest.read_text(encoding="utf-8"))
        if settings["frida"] != FRIDA_VERSION or settings["target"] != target_name():
            raise WorkerError("Worker manifest has the wrong version or platform")
        with executable.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != settings["sha256"]:
            raise WorkerError("Worker file hash does not match its manifest")
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        raise WorkerError(
            "Prepare the native worker before using this backend"
        ) from error
    return executable


class Cancellable:
    """Let the existing bridge timer cancel a worker request on its calling thread."""

    def __init__(self):
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()

    def __enter__(self):
        self.previous = getattr(_current, "cancellable", None)
        _current.cancellable = self
        return self

    def __exit__(self, *args):
        _current.cancellable = self.previous


class Worker:
    """Own one subprocess, its bounded RPC requests and its native handle proxies."""

    def __init__(self, executable, *, output=None):
        # output optionally consumes game bytes in tests; normal runs forward them to the console.
        self.process = subprocess.Popen(
            [str(executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
        )
        self.write_lock = threading.Lock()
        self.call_lock = threading.Lock()
        self.state_lock = threading.RLock()
        self.waiters = {}
        self.sessions = {}
        self.scripts = {}
        self.early_detaches = {}
        self.next_id = 1
        self.failure = None
        self.closed = False
        self.output = output or self._console_output
        self.device = Device(self)
        self.reader = threading.Thread(
            target=self._read, name="core-worker-output", daemon=True
        )
        self.reader.start()
        try:
            hello = self.request("hello")
            if hello != {"protocol": 1, "frida": FRIDA_VERSION}:
                raise WorkerError("Worker protocol or Frida version does not match")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _console_output(pid, fd, data):
        # The game uses separate pipes; its bytes must never be read as RPC messages.
        stream = sys.stdout if fd == 1 else sys.stderr
        binary = getattr(stream, "buffer", None)
        if binary is not None:
            binary.write(data)
        else:
            stream.write(data.decode("utf-8", errors="replace"))
        stream.flush()

    def _write(self, frame):
        data = (
            json.dumps(frame, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        )
        if len(data) > MAX_FRAME:
            raise WorkerError("Worker request exceeds the frame size limit")
        with self.write_lock:
            if self.closed:
                raise WorkerError("Worker is closed")
            try:
                remaining = memoryview(data)
                while remaining:
                    written = self.process.stdin.write(remaining)
                    if not written:
                        raise WorkerError("Worker input pipe stopped accepting data")
                    remaining = remaining[written:]
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                raise WorkerError("Worker input pipe is unavailable") from error

    def _fail(self, error):
        # Wake blocked callers immediately when the pipe or protocol fails.
        with self.state_lock:
            self.failure = error
            for waiter in self.waiters.values():
                if waiter.empty():
                    waiter.put_nowait(error)
        self.device._emit("lost")

    def _read(self):
        try:
            while True:
                data = self.process.stdout.readline(MAX_FRAME + 1)
                if not data:
                    raise WorkerError("Native worker exited or closed its output pipe")
                if len(data) > MAX_FRAME or not data.endswith(b"\n"):
                    raise WorkerError("Invalid or oversized worker response")
                frame = json.loads(data)
                if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
                    raise WorkerError("Invalid worker response")
                with self.state_lock:
                    if "id" in frame:
                        waiter = self.waiters.get(frame["id"])
                        if waiter is None or not waiter.empty():
                            raise WorkerError("Unexpected worker response ID")
                        waiter.put_nowait(frame)
                    else:
                        self._event(frame)
        except Exception as error:
            self._fail(WorkerError(str(error)))

    def _event(self, frame):
        # Native handles are registered before any script is loaded or session is resumed.
        method, params = frame["method"], frame["params"]
        if method == "child-added":
            self.device._emit("child-added", SimpleNamespace(**params))
        elif method == "lost":
            self.device._emit("lost")
        elif method == "output":
            self.output(
                params["pid"],
                params["fd"],
                base64.b64decode(params["data"], validate=True),
            )
        elif method == "detached":
            session = self.sessions.get(params["session"])
            if session is not None:
                session._detached(params["reason"])
            else:
                # A process may exit between attach's reply and proxy registration.
                self.early_detaches[params["session"]] = params["reason"]
        elif method == "message":
            script = self.scripts.get(params["script"])
            if script is not None:
                script._emit("message", params["message"], None)
        else:
            raise WorkerError("Unknown worker event")

    @contextmanager
    def _call_slot(self, deadline, cancellation):
        # Waiting for another caller is part of the deadline, and may be cancelled before sending.
        while True:
            if cancellation and cancellation.cancelled.is_set():
                raise WorkerError("Worker request cancelled before sending")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkerError("Timed out waiting for the worker request slot")
            if self.call_lock.acquire(timeout=min(remaining, 0.01)):
                break
        try:
            yield
        finally:
            self.call_lock.release()

    def request(self, method, params=None, *, timeout=8.0):
        """Send one Core operation, with a deadline and cancellation independent of its event loop."""
        if not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise WorkerError(
                "Worker timeout must be greater than zero and at most ten seconds"
            )
        if threading.current_thread() is self.reader:
            raise WorkerError(
                "Worker callbacks must queue work rather than make synchronous requests"
            )
        # Controller and bridge cleanup share the connection; only one Core call runs at a time.
        deadline = time.monotonic() + timeout
        cancellation = getattr(_current, "cancellable", None)
        with self._call_slot(deadline, cancellation):
            with self.state_lock:
                if self.failure is not None:
                    raise self.failure
                token = self.next_id
                self.next_id += 1
                waiter = queue.Queue(maxsize=1)
                self.waiters[token] = waiter
            try:
                self._write(
                    {
                        "jsonrpc": "2.0",
                        "id": token,
                        "method": method,
                        "params": params or {},
                        "timeout_ms": max(1, int((deadline - time.monotonic()) * 1000)),
                    }
                )
                cancelled = False
                while True:
                    try:
                        response = waiter.get(timeout=0.01)
                        break
                    except queue.Empty:
                        if not cancelled and (
                            time.monotonic() >= deadline
                            or (cancellation and cancellation.cancelled.is_set())
                        ):
                            self._write(
                                {
                                    "jsonrpc": "2.0",
                                    "method": "$/cancelRequest",
                                    "params": {"id": token},
                                }
                            )
                            cancelled = True
                            deadline = time.monotonic() + 2
                        elif cancelled and time.monotonic() >= deadline:
                            error = WorkerError(
                                "Worker did not acknowledge cancellation"
                            )
                            self._fail(error)
                            raise error
                if isinstance(response, Exception):
                    raise response
                if "error" in response:
                    raise WorkerError(response["error"]["message"])
                return response["result"]
            finally:
                with self.state_lock:
                    self.waiters.pop(token, None)

    def close(self):
        """Ask for native cleanup, close the owning pipe and wait for the worker to exit."""
        if self.closed:
            return
        error = None
        try:
            if self.process.poll() is None and self.failure is None:
                self.request("close", timeout=10)
        except WorkerError as failure:
            error = failure
        finally:
            with self.write_lock:
                self.closed = True
                self.process.stdin.close()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Only the helper is terminated; the game is never a kill target.
                self.process.kill()
                self.process.wait(timeout=5)
                error = WorkerError("Native worker cleanup timed out")
            self.reader.join(timeout=2)
            self.process.stdout.close()
        if error is not None:
            raise error
        if self.process.returncode != 0:
            raise WorkerError(
                f"Native worker exited with code {self.process.returncode}"
            )


class Signals:
    """Keep the exact callbacks used by the existing controller and bridge."""

    def __init__(self):
        self.callbacks = {}

    def on(self, signal, callback):
        self.callbacks.setdefault(signal, []).append(callback)

    def off(self, signal, callback):
        self.callbacks[signal].remove(callback)

    def _emit(self, signal, *args):
        # Callbacks only enqueue controller events or answer bridge waiters; they do not call Core.
        for callback in tuple(self.callbacks.get(signal, ())):
            callback(*args)


class Device(Signals):
    """The subset of Frida's local-device API used by our controller."""

    def __init__(self, worker):
        super().__init__()
        self.worker = worker

    def enumerate_processes(self, *, scope="metadata", pids=None):
        params = {} if pids is None else {"pids": list(pids)}
        return [
            SimpleNamespace(**item) for item in self.worker.request("processes", params)
        ]

    def spawn(self, argv, *, envp, cwd, stdio):
        # Preserve the argument list and full environment; no shell performs quoting or expansion.
        return self.worker.request(
            "spawn",
            {
                "argv": list(argv),
                "envp": [f"{k}={v}" for k, v in envp.items()],
                "cwd": cwd,
            },
        )

    def attach(self, pid):
        value = self.worker.request("attach", {"pid": pid})
        session = Session(self.worker, value["handle"], value["detached"])
        with self.worker.state_lock:
            self.worker.sessions[session.handle] = session
            reason = self.worker.early_detaches.pop(session.handle, None)
            if reason is not None:
                session._detached(reason)
        return session

    def resume(self, pid):
        self.worker.request("resume", {"pid": pid})

    def enumerate_pending_children(self):
        return [SimpleNamespace(**item) for item in self.worker.request("pending")]


class Session(Signals):
    """A stable native session handle, separate from the target's reusable PID."""

    def __init__(self, worker, handle, detached):
        super().__init__()
        self.worker, self.handle, self._is_detached = worker, handle, detached
        self.detach_reason = None

    def on(self, signal, callback):
        super().on(signal, callback)
        # Replay an exit observed before the controller could register its callback.
        if signal == "detached" and self.detach_reason is not None:
            callback(self.detach_reason, None)

    def _detached(self, reason):
        self._is_detached = True
        self.detach_reason = reason
        for script in list(self.worker.scripts.values()):
            if script.session is self:
                script.is_destroyed = True
                self.worker.scripts.pop(script.handle, None)
        self.worker.sessions.pop(self.handle, None)
        self._emit("detached", reason, None)

    @property
    def is_detached(self):
        # Query Core when needed: its detach signal can arrive after an operation's error reply.
        if (
            not self._is_detached
            and self.worker.failure is None
            and not self.worker.closed
        ):
            try:
                observed = self.worker.request(
                    "session.state", {"session": self.handle}
                )
            except WorkerError:
                # Core may have retired the handle after publishing the detach event.
                if not self._is_detached:
                    raise
            else:
                # An older reply must not overwrite a newer event's state.
                with self.worker.state_lock:
                    self._is_detached = self._is_detached or observed
        return self._is_detached

    def enable_child_gating(self):
        self.worker.request("session.enable", {"session": self.handle})

    def disable_child_gating(self):
        self.worker.request("session.disable", {"session": self.handle})

    def detach(self):
        self.worker.request("session.detach", {"session": self.handle})
        self._is_detached = True

    def create_script(self, source, *, name):
        handle = self.worker.request(
            "session.create", {"session": self.handle, "source": source}
        )
        script = Script(self.worker, self, handle)
        with self.worker.state_lock:
            self.worker.scripts[handle] = script
        return script


class Script(Signals):
    """Forward script operations without exposing native addresses to the controller."""

    def __init__(self, worker, session, handle):
        super().__init__()
        self.worker, self.session, self.handle = worker, session, handle
        self._is_destroyed = False

    @property
    def is_destroyed(self):
        if (
            not self._is_destroyed
            and self.worker.failure is None
            and not self.worker.closed
        ):
            try:
                observed = self.worker.request("script.state", {"script": self.handle})
            except WorkerError:
                if not self._is_destroyed:
                    raise
            else:
                with self.worker.state_lock:
                    self._is_destroyed = self._is_destroyed or observed
        return self._is_destroyed

    @is_destroyed.setter
    def is_destroyed(self, value):
        self._is_destroyed = value

    def load(self):
        self.worker.request("script.load", {"script": self.handle})

    def post(self, message):
        self.worker.request("script.post", {"script": self.handle, "message": message})

    def unload(self):
        self.worker.request("script.unload", {"script": self.handle})
        self.is_destroyed = True
        with self.worker.state_lock:
            self.worker.scripts.pop(self.handle, None)


class Backend:
    """Keep the worker behind the same entry points used by the Python Frida backend."""

    __version__ = FRIDA_VERSION
    backend_name = "native Frida Core worker"
    Cancellable = Cancellable

    def __init__(self, executable, *, output=None):
        self.worker = Worker(executable, output=output)

    def get_local_device(self):
        return self.worker.device

    def close(self):
        self.worker.close()
