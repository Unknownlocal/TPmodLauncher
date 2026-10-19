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

# Dependency order is also the execution order in the game's private namespace.
# Keep this list fixed: packs and source mods cannot contribute host modules.
# The callable files skip relative imports on that path; the package __init__
# serves normal host imports and is deliberately absent from this execution list.
PYTHON_SOURCES = (
    "manager", "callables/fingerprint", "callables/target",
    "callables/result_contract", "callables/binding", "callables/adapters",
    "regulations/framing", "regulations/channel",
    "pack_discovery", "menu", "settings", "themes",
)


class BridgeError(RuntimeError):
    """Raised when the bridge cannot initialise or cleanly stop."""


class Bridge:
    """Own a Frida script carrying fixed control operations and declarative UI data."""

    def __init__(self, frida, session, source, on_message):
        # session is already path-verified; source is our fixed script; callback queues diagnostic events.
        self.frida, self.session = frida, session
        self.script = session.create_script(source, name="modLauncher-hook-bridge")
        self.loaded = False
        self.waiters = {}
        self._on_message = on_message
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

    def wake(self):
        """Wake the controller queue without native RPC or guest execution."""
        self._on_message({"type": "send", "payload": {"event": "regulation-wake"}})

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

    def request(self, operation, timeout=2.0, *, buttons=None, loading=None, settings=None, themes=None):
        """Send a fixed operation, waiting at most timeout seconds for a safe Python entry."""
        if operation not in ("status", "shutdown", "menu", "loading", "settings", "themes"):
            raise BridgeError("Unknown bridge operation")
        if operation == "menu":
            if type(buttons) is not list or len(json.dumps(buttons)) > 16_384:
                raise BridgeError("Expected bounded menu declarations")
        elif buttons is not None:
            raise BridgeError("This operation accepts no menu declarations")
        if operation == "loading":
            if type(loading) is not dict or len(json.dumps(loading)) > 1024:
                raise BridgeError("Expected a bounded loading record")
        elif loading is not None:
            raise BridgeError("This operation accepts no loading record")
        if operation == "settings":
            if type(settings) is not dict or len(json.dumps(settings).encode("utf-8")) > 65_536:
                raise BridgeError("Expected bounded settings declarations")
        elif settings is not None:
            raise BridgeError("This operation accepts no settings declarations")
        if operation == "themes":
            if type(themes) is not list or len(json.dumps(themes).encode("utf-8")) > 16_384:
                raise BridgeError("Expected bounded theme declarations")
        elif themes is not None:
            raise BridgeError("This operation accepts no theme declarations")
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
            message = {"type": "manager-request", "operation": operation, "id": token}
            if operation == "menu":
                message["buttons"] = buttons
            if operation == "loading":
                message["loading"] = loading
            if operation == "settings":
                message["settings"] = settings
            if operation == "themes":
                message["themes"] = themes
            self._post(message, timeout)
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

    def set_menu(self, buttons):
        """Apply a complete declarative registry; an exited game needs no native restoration."""
        if self.session.is_detached:
            return None
        return self.request("menu", buttons=buttons)

    def set_settings(self, records, result=None):
        """Publish owned tabs and optionally acknowledge one native save request."""
        if self.session.is_detached:
            return None
        return self.request("settings", settings={"records": records, "result": result})

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

    def set_themes(self, records):
        if self.session.is_detached:
            return None
        return self.request("themes", themes=records)

    def set_loading(self, loading):
        """Send trusted startup progress on the same serialized request channel as menu updates."""
        if self.session.is_detached:
            return None
        result = self.request("loading", loading=loading)
        if result["menu"]["state"] == "failed":
            raise BridgeError(
                "Native readiness guard unavailable: " + result["menu"]["detail"]
            )
        return result


def factory(root, frida):
    """Read our trusted source files once and return a factory for per-process bridges."""
    root = Path(root).resolve()

    def read(relative):
        path = (root / relative).resolve()
        # Source files must stay inside this repository, including resolved links.
        if not path.is_relative_to(root):
            raise BridgeError("Bridge source path escapes the launcher folder")
        return path.read_text(encoding="utf-8-sig")

    # Only these trusted infrastructure sources enter the game; packs cannot add entries.
    sources = {
        f"modLauncher/hook_manager/{name}.py": read(f"hook_manager/{name}.py")
        for name in PYTHON_SOURCES
    }
    bootstrap_source = read("hook_manager/bootstrap.py")
    script = read("hook_manager/bridge.js")
    digest = hashlib.sha256(json.dumps(sources).encode("utf-8")).hexdigest()

    def create(session, pid, on_message, *, bootstrap=True):
        # Each process gets a distinct owner token; only the trusted controller constructs this source.
        arguments = ",".join(
            json.dumps(value)
            for value in (sources, digest, uuid.uuid4().hex, str(root.parent))
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
