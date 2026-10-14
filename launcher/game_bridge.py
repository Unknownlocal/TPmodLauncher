# SPDX-License-Identifier: MPL-2.0
"""Load the trusted bridge into owned sessions and bound its shutdown requests."""

import hashlib
import json
import math
import queue
from pathlib import Path
import threading
import time
import uuid


class BridgeError(RuntimeError):
    """Raised when the bridge cannot initialise or cleanly stop."""


class Bridge:
    """Own one Frida script; its messages accept status/shutdown names, never code or game objects."""

    def __init__(self, frida, session, source, on_message):
        # session is already path-verified; source is our fixed script; callback queues diagnostic events.
        self.frida, self.session = frida, session
        self.script = session.create_script(source, name="modLauncher-hook-bridge")
        self.loaded = False
        self.waiters = {}
        self.lock = threading.Lock()
        self.script.on(
            "message", lambda message, data: self._message(message, on_message)
        )

    def _message(self, message, on_message):
        # Frida callbacks only deliver data to waiting callers or the controller's event queue.
        payload = message.get("payload", {})
        if (
            message.get("type") == "send"
            and isinstance(payload, dict)
            and payload.get("event") == "reply"
        ):
            with self.lock:
                waiter = self.waiters.get(payload.get("id"))
                if waiter is not None and waiter.empty():
                    waiter.put_nowait(payload)
        else:
            on_message(message)

    def start(self):
        # Loading observes the interpreter; Python runs later at a main-thread GIL boundary.
        self.script.load()
        self.loaded = True

    def _post(self, message, timeout):
        # Frida's native send supports cancellation; its synchronous script RPC wait does not.
        cancellable = self.frida.Cancellable()
        timer = threading.Timer(timeout, cancellable.cancel)
        timer.daemon = True
        timer.start()
        try:
            with cancellable:
                self.script.post(message)
        finally:
            timer.cancel()

    def request(self, operation, timeout=2.0):
        """Send a status/shutdown name, waiting at most timeout seconds for a safe Python entry."""
        if operation not in ("status", "shutdown"):
            raise BridgeError("Unknown bridge operation")
        if not math.isfinite(timeout) or timeout <= 0:
            raise BridgeError("Bridge timeout must be finite and greater than zero")
        token = uuid.uuid4().hex
        waiter = queue.Queue(maxsize=1)
        deadline = time.monotonic() + timeout
        answered = False
        with self.lock:
            if self.waiters:
                raise BridgeError("A bridge request is already pending")
            self.waiters[token] = waiter
        try:
            self._post(
                {"type": "manager-request", "operation": operation, "id": token},
                timeout,
            )
            response = waiter.get(timeout=max(0, deadline - time.monotonic()))
            answered = True
            if "error" in response:
                raise BridgeError(response["error"])
            return response["status"]
        except queue.Empty as error:
            raise BridgeError(
                "Timed out waiting for Python main-thread entry"
            ) from error
        finally:
            with self.lock:
                self.waiters.pop(token, None)
            # Cancel any leftover target-side request, including after a timeout or send failure.
            if (
                not answered
                and not self.session.is_detached
                and not self.script.is_destroyed
            ):
                try:
                    self._post({"type": "manager-cancel", "id": token}, 0.25)
                except Exception:
                    pass  # close() still unloads the script after a failed cleanup request.

    def close(self):
        """Stop the manager on its thread before unloading our native transport."""
        if self.session.is_detached or not self.loaded:
            return  # No live script remains here, or loading failed before the manager could start.
        if self.script.is_destroyed:
            raise BridgeError(
                "Bridge script disappeared; live manager cleanup cannot be confirmed"
            )
        try:
            self.request("shutdown")
        finally:
            # Unload also detaches the interceptor even if the bounded shutdown failed.
            if not self.session.is_detached and not self.script.is_destroyed:
                self.script.unload()


def factory(root, frida):
    """Read our three source files once and return a factory for per-process bridges."""
    root = Path(root).resolve()

    def read(relative):
        path = (root / relative).resolve()
        # Source files must stay inside this repository, including resolved links.
        if not path.is_relative_to(root):
            raise BridgeError("Bridge source path escapes the launcher folder")
        return path.read_text(encoding="utf-8-sig")

    manager = read("hook_manager/manager.py")
    bootstrap_source = read("hook_manager/bootstrap.py")
    script = read("hook_manager/bridge.js")
    digest = hashlib.sha256(manager.encode("utf-8")).hexdigest()

    def create(session, pid, on_message, *, bootstrap=True):
        # Each process gets a distinct owner token; only the trusted controller constructs this source.
        arguments = ",".join(
            json.dumps(value) for value in (manager, digest, uuid.uuid4().hex)
        )
        # Build one fixed expression; the transport can append only a quoted JSON request.
        prefix = (
            "(lambda ns: (exec(compile("
            + json.dumps(bootstrap_source)
            + ', "modLauncher/hook_manager/bootstrap.py", "exec"), ns), ns["pulse"]('
            + arguments
            + ","
        )
        suffix = '))[1])({"__builtins__": __builtins__})'
        # A fork transition loads the script engine for exec gating, but must not enter inherited Python.
        config = json.dumps(
            {"prefix": prefix, "suffix": suffix, "bootstrap": bootstrap}
        )
        return Bridge(
            frida, session, script.replace("__HOOK_CONFIG__", config, 1), on_message
        )

    return create
