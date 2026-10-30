# SPDX-License-Identifier: MPL-2.0
"""Wrap approved game methods and pass plain values to callbacks."""

from functools import update_wrapper
import importlib.util
import inspect
import math
import re
import sys
import threading
from types import ModuleType


class HookError(RuntimeError):
    """Raised when a target, registration or callback fails its checks."""


def identifier(value):
    # Public requests use names, never module paths, object references or code strings.
    if not isinstance(value, str) or not re.fullmatch(
        r"[a-z][a-z0-9_.:-]{0,127}", value
    ):
        raise HookError("Expected a short named identifier")
    return value


def data_copy(value, depth=0, budget=None):
    """Copy bounded JSON-compatible data, rejecting Python objects and non-finite numbers."""
    if budget is None:
        budget = [1024]
    budget[0] -= 1
    if budget[0] < 0 or depth > 8:
        raise HookError("Callback data exceeds its size/depth limit")
    kind = type(value)
    if value is None or kind is bool:
        return value
    if kind is int and -(2**63) <= value < 2**63:
        return value
    if kind is float and math.isfinite(value):
        return value
    if kind is str and len(value) <= 4096:
        return value
    if kind is list:
        return [data_copy(item, depth + 1, budget) for item in value]
    if kind is dict and all(type(key) is str and len(key) <= 128 for key in value):
        return {key: data_copy(item, depth + 1, budget) for key, item in value.items()}
    raise HookError("Callback data must contain only bounded plain values")


class Target:
    """Describe an approved callable; only trusted host code constructs these descriptors."""

    def __init__(self, name, module, owners, attribute, signature, *, replace=False):
        # owners lists class names below the module; signature is inspect.signature's exact display.
        self.name = identifier(name)
        if (
            not isinstance(module, str)
            or not module
            or not all(part.isidentifier() for part in module.split("."))
        ):
            raise HookError("Invalid approved module name")
        if not isinstance(owners, (tuple, list)):
            raise HookError("Approved owner path must be a list or tuple")
        if not all(
            isinstance(part, str) and part.isidentifier()
            for part in (*owners, attribute)
        ):
            raise HookError("Invalid approved attribute path")
        if not isinstance(signature, str) or type(replace) is not bool:
            raise HookError("Invalid target signature/replacement permission")
        self.module = module
        self.owners = tuple(owners)
        self.attribute = attribute
        self.signature = signature
        self.replace = replace

    def resolve(self, completed):
        # Read only already-loaded modules; never import a game module just to install a hook.
        module = sys.modules.get(self.module)
        if module is None:
            return None
        if type(module) is not ModuleType:
            raise HookError("Deferred/custom module objects are unsupported")
        spec = getattr(module, "__spec__", None)
        if (
            getattr(spec, "_initializing", False)
            and completed.get(self.module) is not module
        ):
            return None
        owner = module
        for part in self.owners:
            owner = inspect.getattr_static(owner, part)
        if not isinstance(owner, (ModuleType, type)):
            raise HookError("Approved hook owner must be a module or class")
        raw = inspect.getattr_static(owner, self.attribute)
        descriptor = type(raw) if isinstance(raw, (staticmethod, classmethod)) else None
        original = raw.__func__ if descriptor else raw
        signature = self._check(original)
        return owner, raw, original, descriptor, signature

    def _check(self, original):
        """Check callable compatibility and return its accepted signature."""
        if (
            not callable(original)
            or hasattr(original, "__wrapped__")
            or hasattr(original, "_tpm_hook_group")
        ):
            raise HookError(f"Target {self.name} is not an unwrapped callable")
        signature = inspect.signature(original)
        if str(signature) != self.signature:
            raise HookError(f"Target {self.name} signature changed: {signature}")
        return signature


class _Loader:
    # Delegate module execution, then notify the manager before import returns to its consumer.
    def __init__(self, original, manager, name):
        self.original, self.manager, self.name = original, manager, name

    def create_module(self, spec):
        create = getattr(self.original, "create_module", None)
        return create(spec) if create is not None else None

    def exec_module(self, module):
        self.original.exec_module(module)
        # A shutdown during module execution must leave the normal import usable.
        if self.manager.closed:
            return
        self.manager.completed[self.name] = module
        # Worker imports wait for the next main-thread poll rather than installing from that worker.
        if threading.current_thread() is threading.main_thread():
            self.manager.poll()

    def __getattr__(self, name):
        return getattr(self.original, name)


class _Finder:
    def __init__(self, manager):
        self.manager = manager

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.manager.wanted:
            return None
        finders = tuple(sys.meta_path)
        if self not in finders:
            return None
        # Delegate forward only, preserving the game's importer and avoiding recursive lookup.
        for finder in finders[finders.index(self) + 1 :]:
            find = getattr(finder, "find_spec", None)
            if find is None:
                continue
            spec = find(fullname, path, target)
            if spec is None:
                continue
            if (
                spec.loader is None
                or not callable(getattr(spec.loader, "exec_module", None))
                or isinstance(spec.loader, importlib.util.LazyLoader)
            ):
                self.manager.reject_module(fullname)
            else:
                spec.loader = _Loader(spec.loader, self.manager, fullname)
            return spec
        return None


class Manager:
    """Register approved targets and restore the wrappers owned by each hook group."""

    def __init__(self, dispatch=None):
        # dispatch is a trusted host adapter used by synthetic tests; no mod loader supplies it.
        self.dispatch = dispatch
        self.targets = {}
        self.groups = {}
        self.completed = {}
        self.rejected = set()
        self.wanted = set()
        self.observers = {}
        self.finder = _Finder(self)
        self.errors = []
        self.closed = False

    def _main(self):
        # Registration and restoration must happen on the game's Python main thread.
        if threading.current_thread() is not threading.main_thread():
            raise HookError("Hook registration requires the Python main thread")
        if self.closed:
            raise HookError("Hook manager is stopped")

    def store_error(self, error):
        """Retain recent error text for status checks without writing a log."""
        # Bound retained diagnostics; errors must not grow indefinitely or expose raw objects.
        self.errors.append(str(error)[:4096])
        del self.errors[:-16]

    def approve(self, target):
        """Add a host-owned target descriptor without importing or changing its module."""
        self._main()
        if not isinstance(target, Target) or target.name in self.targets:
            raise HookError("Invalid or duplicate target approval")
        self.targets[target.name] = target

    def _watch(self):
        self.wanted = {
            self.targets[binding["target"]].module
            for group in self.groups.values()
            if group["state"] == "pending"
            for binding in group["bindings"]
        }
        self.wanted.update(self.observers)
        # Leave normal imports untouched when no pending target needs observation.
        if self.wanted and self.finder not in sys.meta_path:
            sys.meta_path.insert(0, self.finder)
        elif not self.wanted and self.finder in sys.meta_path:
            sys.meta_path.remove(self.finder)

    def observe(self, module, callback):
        """Run a trusted one-shot adapter after a normal module import, before its consumer."""
        self._main()
        if (
            not isinstance(module, str)
            or not all(part.isidentifier() for part in module.split("."))
            or not callable(callback)
        ):
            raise HookError("Invalid trusted import observer")
        self.observers[module] = callback
        self.poll()

    def install(self, name, bindings):
        """Register a group of target/callback/phase records and install once all targets are ready."""
        self._main()
        identifier(name)
        if not isinstance(bindings, list) or not 1 <= len(bindings) <= 64:
            raise HookError("Expected 1 to 64 hook bindings")
        bindings = data_copy(bindings)
        for binding in bindings:
            if (
                type(binding) is not dict
                or set(binding) != {"target", "callback", "phase"}
                or type(binding["target"]) is not str
                or binding["target"] not in self.targets
                or binding["phase"] not in ("before", "after")
            ):
                raise HookError(
                    "Binding requires an approved target, callback and before/after phase"
                )
            identifier(binding["target"])
            identifier(binding["callback"])
        if len({binding["target"] for binding in bindings}) != len(bindings):
            raise HookError("A group cannot bind the same target twice")
        existing = self.groups.get(name)
        if existing is not None:
            if existing["bindings"] == bindings and existing["state"] in (
                "pending",
                "active",
            ):
                return self.status()
            raise HookError("Group name is already registered")
        if not callable(self.dispatch):
            raise HookError("No callback dispatcher has been configured")
        self.groups[name] = {"state": "pending", "bindings": bindings, "changes": []}
        try:
            self._install_ready(name)
        except Exception as error:
            self._disable(name, error)
            raise HookError(f"Cannot install group {name}: {error}") from error
        finally:
            self._watch()
        return self.status()

    def _wrapper(self, name, binding, target, original, signature):
        # Keep original callables and self/cls references in the trusted host, outside callback data.
        group = self.groups[name]

        def wrapped(*args, **kwargs):
            if (
                group["state"] != "active"
                or threading.current_thread() is not threading.main_thread()
            ):
                return original(*args, **kwargs)
            # Call the original outside callback error handling, so its exceptions never trigger a retry.
            after = binding["phase"] == "after"
            result = original(*args, **kwargs) if after else None
            replacement = False
            try:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                arguments = {
                    key: value
                    for key, value in bound.arguments.items()
                    if key not in ("self", "cls")
                }
                record = data_copy(
                    {
                        "group": name,
                        "target": target.name,
                        "callback": binding["callback"],
                        "phase": binding["phase"],
                        "arguments": arguments,
                        "result": result,
                    }
                )
                decision = data_copy(self.dispatch(record))
                if group["state"] != "active":
                    decision = {
                        "action": "continue"
                    }  # Removal during dispatch cancels its decision.
                if decision == {"action": "continue"}:
                    pass
                elif (
                    type(decision) is dict
                    and set(decision) == {"action", "value"}
                    and decision["action"] == "replace"
                    and target.replace
                ):
                    result = decision["value"]
                    replacement = True
                else:
                    raise HookError("Callback returned an unsupported decision")
            except Exception as error:
                # Disable our group after a callback failure, keeping the game's normal call/result.
                self._disable(name, error)
            return result if after or replacement else original(*args, **kwargs)

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped

    def _install_ready(self, name):
        # Resolve/check the whole group first, then journal each change for rollback.
        group = self.groups[name]
        resolved = [
            (
                binding,
                self.targets[binding["target"]],
                self.targets[binding["target"]].resolve(self.completed),
            )
            for binding in group["bindings"]
        ]
        if any(found is None for _, _, found in resolved):
            return
        addresses = {(id(found[0]), target.attribute) for _, target, found in resolved}
        if len(addresses) != len(resolved):
            raise HookError("Two approved identifiers resolve to the same callable")
        for binding, target, found in resolved:
            owner, raw, original, descriptor, signature = found
            wrapped = self._wrapper(name, binding, target, original, signature)
            installed = descriptor(wrapped) if descriptor else wrapped
            if inspect.getattr_static(owner, target.attribute) is not raw:
                raise HookError("Target changed during group installation")
            # Journal before assignment so a metaclass that writes and then raises can still be restored.
            group["changes"].append(
                (
                    owner,
                    target.attribute,
                    raw,
                    installed,
                    target.attribute in vars(owner),
                )
            )
            setattr(owner, target.attribute, installed)
        group["state"] = "active"

    def _disable(self, name, error=None):
        # Disable callbacks first, then undo only attributes that still contain our exact wrapper.
        group = self.groups[name]
        group["state"] = "failed" if error is not None else "removed"
        if error is not None:
            self.store_error(error)
        for owner, attribute, raw, installed, local in reversed(group["changes"]):
            try:
                current = inspect.getattr_static(owner, attribute)
                if current is raw:
                    continue  # The assignment may have failed before changing anything.
                if current is not installed:
                    raise HookError(
                        f"Restoration skipped for {attribute}: another owner replaced it"
                    )
                if local:
                    setattr(owner, attribute, raw)
                else:
                    delattr(owner, attribute)
            except Exception as failure:
                self.store_error(failure)
        group["changes"].clear()
        self._watch()

    def remove(self, name):
        """Disable a group's callbacks and restore only attributes still owned by that group."""
        self._main()
        if name not in self.groups:
            raise HookError("Unknown hook group")
        self._disable(name)
        return self.status()

    def poll(self):
        """Install ready pending groups while preserving normal import failures and game behavior."""
        self._main()
        for module in tuple(self.rejected):
            self._reject_pending(module)
        self.rejected.clear()
        for name, callback in list(self.observers.items()):
            module = sys.modules.get(name)
            if type(module) is ModuleType and (
                not getattr(getattr(module, "__spec__", None), "_initializing", False)
                or self.completed.get(name) is module
            ):
                try:
                    if callback(module):
                        self.observers.pop(name, None)
                except Exception as error:
                    self.observers.pop(name, None)
                    self.store_error(error)
        for name, group in list(self.groups.items()):
            if group["state"] == "pending":
                try:
                    self._install_ready(name)
                except Exception as error:
                    self._disable(name, error)
        self._watch()
        return self.status()

    def reject_module(self, module):
        # A worker's loader can report failure, but only the main thread may restore hooks.
        self.rejected.add(module)
        if threading.current_thread() is threading.main_thread():
            self._reject_pending(module)
            self.rejected.discard(module)

    def _reject_pending(self, module):
        # Unsupported deferred loaders remain the game's responsibility; fail only our pending groups.
        for name, group in list(self.groups.items()):
            if group["state"] == "pending" and any(
                self.targets[b["target"]].module == module for b in group["bindings"]
            ):
                self._disable(
                    name, HookError(f"Deferred loader is unsupported: {module}")
                )

    def status(self):
        """Return plain diagnostic data without exposing descriptors, callables or game objects."""
        return {
            "state": "stopped" if self.closed else "ready",
            "targets": len(self.targets),
            "groups": {name: group["state"] for name, group in self.groups.items()},
            "errors": list(self.errors),
        }

    def stop(self):
        """Remove every owned group and import observer, then mark this manager stopped."""
        if self.closed:
            return self.status()
        self._main()
        for name in reversed(list(self.groups)):
            self._disable(name)
        self.observers.clear()
        self._watch()
        self.closed = True
        return self.status()

    def request(self, record):
        """Handle the foundation's status/shutdown requests using plain records only."""
        record = data_copy(record)
        if type(record) is not dict or set(record) != {"operation"}:
            raise HookError("Expected one named operation")
        if record["operation"] == "status":
            return self.poll() if not self.closed else self.status()
        if record["operation"] == "shutdown":
            return self.stop()
        raise HookError("Unknown manager operation")
