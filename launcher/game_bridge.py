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

from .regulations.bridge_converter import convert_bridges

# Dependency order is also the execution order in the game's private namespace.
# Keep this list fixed: packs and source mods cannot contribute host modules.
# The callable files skip relative imports on that path; the package __init__
# serves normal host imports and is deliberately absent from this execution list.
PYTHON_SOURCES = (
    "manager", "callables/signatures", "callables/target",
    "callables/refusals", "callables/binding", "callables/adapters",
    # Complete adapters and their transport load once as private modules.
    "pack_discovery", "career_preload", "menu", "settings", "themes", "adapter_modules",
)

# Closed source groups, not a filesystem scan: adding a file to a pack or even
# this folder cannot extend what the game loads. Python resolves dependencies
# inside this bundle using the modules' existing relative imports.
ADAPTER_SOURCES = {
    "": "__init__ employment native_values",
    "ui": "__init__ bindings scroll_text dialog",
    "drivers": "__init__ targets methods snapshot terms query_inputs queries planner rookie_check",
    "staff": "__init__ targets methods planner prestige_switch staff_signing staffing_methods staffing_plan staffing_publication staffing",
    "personnel": "__init__ caller_targets callers cancellation preparation preparation_methods market_graph market_mail market_preparation approach offer rollover native_operation",
    "personnel/feedback": "__init__ bindings notice adapter",
    "academy": "__init__ targets methods terms planner caller_targets callers feedback preparation preparation_methods",
    "projects": "__init__ targets methods snapshot technical_profile planner caller_targets callers",
    "regulations": "__init__ framing protocol channel contexts outcome guards refused_proposals regulation_approach regulation_personnel regulation_academy regulation_project staffing_result staffing_coordinator regulation_native_switches regulation_selection regulation_policy regulation_category regulation_scripts regulation_native_defaults regulation_screen automatic_progress automatic_announcements automatic_changes regulation_change regulation_voting",
}


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

    def request(self, operation, timeout=2.0, *, buttons=None, loading=None, settings=None, themes=None,
                regulations=None, _service=None):
        """Await a fixed operation; preparation also services its bounded publication."""
        if operation not in ("status", "shutdown", "menu", "loading", "settings", "themes", "adapters", "regulation-defaults",
                             "await-regulations", "prepare-regulations", "install-regulations", "activate-regulations", "stop-regulations", "retire-regulations", "restore-native-rules"):
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
        if operation == "await-regulations":
            if (regulations is not None and (type(regulations) is not dict
                    or set(regulations) - {"pack", "categories", "scripted_changes", "automatic_changes", "voting"}
                    or not {"pack", "categories"} <= regulations.keys()
                    or type(regulations["pack"]) is not str or not Path(regulations["pack"]).is_absolute()
                    or type(regulations["categories"]) is not list
                    or not 0 <= len(regulations["categories"]) <= 128
                    or type(regulations.get("voting", False)) is not bool
                    or not (regulations["categories"] or regulations.get("voting"))
                    or type(regulations.get("scripted_changes", [])) is not list
                    or len(regulations.get("scripted_changes", [])) > 4096
                    or type(regulations.get("automatic_changes", {})) is not dict
                    or len(regulations.get("automatic_changes", {})) > 128
                    # Owner-qualified script IDs can grow the admitted 1 MiB
                    # file data; this bound includes that preparation overhead.
                    or len(json.dumps(regulations, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")) > 2 * 1024 * 1024)) or _service is not None:
                raise BridgeError("Expected bounded prepared regulation categories")
        elif operation in ("prepare-regulations", "install-regulations", "activate-regulations", "stop-regulations", "retire-regulations", "restore-native-rules"):
            fields = {"generation", "endpoint"}
            if operation == "install-regulations" and type(regulations) is dict and "native_rules" in regulations:
                fields.add("native_rules")
            if (type(regulations) is not dict or set(regulations) != fields
                    or type(regulations["generation"]) is not int or regulations["generation"] < 1
                    or type(regulations["endpoint"]) is not dict or set(regulations["endpoint"]) != {"port", "token"}
                    or len(json.dumps(regulations).encode("utf-8")) > 2048
                    or (not callable(_service) if operation in ("prepare-regulations", "retire-regulations", "activate-regulations") else _service is not None)):
                raise BridgeError("Expected bounded regulation setup and controller service")
            if "native_rules" in fields:
                values = regulations["native_rules"]
                try:
                    convert_bridges(values)
                except ValueError as error:
                    raise BridgeError("Expected native regulation check booleans or rule references") from error
        elif regulations is not None or _service is not None:
            raise BridgeError("This operation accepts no regulation setup or service")
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
            if regulations is not None:
                message["regulations"] = regulations
            self._post(message, timeout)
            while True:
                remaining = max(0, deadline - time.monotonic())
                try:
                    response = waiter.get(timeout=min(.005, remaining) if _service else remaining)
                    break
                except queue.Empty:
                    if _service is None or time.monotonic() >= deadline:
                        raise
                    # The game waits for policy acknowledgements before replying
                    # here. Service only regulation transport/guest work: PackMods
                    # polling could send nested UI requests to this busy bridge.
                    _service()
            answered = True
            if "error" in response:
                raise BridgeError(response["error"])
            return response["status"]
        except queue.Empty as error:
            raise BridgeError(
                f"Timed out waiting for Python main-thread entry ({operation})"
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

    def regulation_defaults(self, pack):
        """Accept plain defaults only for this scope's already selected pack."""
        # The game chooses the folder from startup discovery. This argument only
        # verifies the reply; it cannot request another folder or native target.
        defaults = self.request("regulation-defaults", timeout=10).get("regulation_defaults")
        if (type(defaults) is not dict or set(defaults) != {"pack", "categories"}
                or defaults["pack"] != str(pack) or type(defaults["categories"]) is not list
                or len(defaults["categories"]) > 128
                or len(json.dumps(defaults["categories"], ensure_ascii=False, allow_nan=False,
                                  separators=(",", ":")).encode("utf-8")) > 1024 * 1024):
            raise BridgeError("Native regulation defaults were not confirmed for the selected pack")
        return defaults["categories"]

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
    adapters = {}
    for folder, names in ADAPTER_SOURCES.items():
        for name in names.split():
            relative = "/".join(part for part in ("game_adapters", folder, name) if part)
            package = name == "__init__"
            module = relative.removesuffix("/__init__").replace("/", ".")
            filename = "modLauncher/hook_manager/" + relative + ".py"
            adapters[module] = (filename, read("hook_manager/" + relative + ".py"), package)
    # Keep bundled source as data in the bootstrap namespace. It is imported
    # only by the trusted 'adapters' operation, after normal bootstrap is ready.
    sources["modLauncher/hook_manager/adapter_modules.py"] += "\nADAPTER_SOURCES = " + repr(adapters) + "\n"
    bootstrap_source = read("hook_manager/bootstrap.py")
    script = read("hook_manager/bridge.js")
    digest = hashlib.sha256(json.dumps(sources).encode("utf-8")).hexdigest()

    def create(session, pid, on_message, *, bootstrap=True):
        # Each process gets a distinct owner token; only the trusted controller constructs this source.
        instance = uuid.uuid4().hex
        arguments = ",".join(
            json.dumps(value)
            for value in (sources, digest, instance, str(root.parent))
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
        # Once the private owner exists, source strings are unnecessary. Keep
        # the same digest/instance checks in pulse, with an empty source argument.
        repeat_prefix = "__import__('sys').modules['_tpm_hook_manager_host'].pulse(" + ",".join(
            json.dumps(value) for value in ({}, digest, instance, str(root.parent))) + ","
        # A fork transition loads the script engine for exec gating, but must not enter inherited Python.
        config = json.dumps(
            {"prefix": prefix, "suffix": suffix, "repeat_prefix": repeat_prefix, "bootstrap": bootstrap}
        )
        return Bridge(
            frida, session, script.replace("__HOOK_CONFIG__", config, 1), on_message
        )

    return create
