# SPDX-License-Identifier: MPL-2.0
"""Load bundled game adapters as private modules without changing the game path."""

import importlib
import importlib.util
import sys
from contextlib import ExitStack
from types import ModuleType

if __package__:
    from .manager import HookError


class AdapterModules:
    """Own one bridge's fixed module bundle and its temporary import finder.

    The launcher supplies these sources, not packs or control requests. Separate
    module dictionaries keep identically named helpers in different adapters
    independent. The existing manager/callable classes are shared by reference.
    Loading definitions installs no game hooks and grants no policy readiness.
    """

    def __init__(self, host, sources):
        self.host, self.sources = host, sources
        self.prefix = host.__name__ + ".adapter_code"
        self.modules = {}
        self.state = "idle"
        self.error = None
        self.regulations = []
        self.channel = None
        self.coverage = {}
        self.regulation_error = None
        self.policy = None

    def status(self):
        """Report loaded definitions and initial hook coverage, not policy readiness."""
        result = {"state": self.state, "modules": len(self.sources) if self.state == "loaded" else 0}
        if self.error is not None:
            result["detail"] = self.error
        if self.channel is not None:
            # Installation coverage is not policy readiness. In particular,
            # signing adapters do not install whole-team staffing callers.
            result["regulations"] = {"state": "cleanup_pending" if self.regulation_error else "installed",
                                     "components": dict(self.coverage)}
            if self.regulation_error:
                result["regulations"]["detail"] = self.regulation_error
        return result

    def install_regulations(self, state, channel, select, *, native_rules, report):
        """Own the fixed adapters for a career supplied by trusted native setup.

        This Python-only method is absent from the bridge request API. The
        caller supplies the identified career, authenticated channel and native
        rule selector; no mod paths or author Python enter this loader. Channel
        ownership transfers after preflight, including cleanup on failed setup.
        Automatic career selection and policy publication are separate steps.
        """
        self.host.manager._main()
        if self.state != "loaded" or self.channel is not None:
            raise HookError("Regulation installation needs an unused loaded adapter bundle")
        game = sys.modules.get("game_state")
        career = getattr(game, "GameState", None)
        if not isinstance(career, type) or not isinstance(state, career):
            raise HookError("Regulation installation needs a native GameState")
        if channel is None or channel.closed or not callable(select) or not callable(report):
            raise HookError("Regulation installation needs an open channel and trusted callbacks")
        adapters = self.host.adapter_code.game_adapters.regulations
        self.channel = channel
        try:
            # Switches validate their entire boolean record first. Planners can
            # still retain the original rookie helper after its switch installs.
            self.regulations.extend(adapters.native_rules.install_native_rules(state, native_rules, report=report))
            for module, name in (("personnel_adapter", "PersonnelAdapter"),
                                 ("academy_adapter", "AcademyAdapter"),
                                 ("project_adapter", "ProjectAdapter")):
                installed = getattr(getattr(adapters, module), name).create(state, channel, select, report)
                if installed is not None:
                    self.regulations.append(installed)
                if name == "PersonnelAdapter":
                    for kind in ("driver", "staff"):
                        self.coverage[kind + "_signing"] = installed is not None and kind in installed.planners
                else:
                    self.coverage["academy_signing" if name == "AcademyAdapter" else "development_projects"] = installed is not None
            groups = {name for hook in self.regulations for name, row in hook.groups.items() if row["state"] == "active"}
            # A requested bypass is "disabled" only if its wrapper installed.
            # "skipped" means compatibility checks left that native rule intact.
            for key, group in (("rookie_contract_length_check", "drivers.rookie-check"),
                               ("staff_prestige_check", "staff.prestige-check")):
                self.coverage[key] = "native" if native_rules.get(key, True) else "disabled" if group in groups else "skipped"
            # The coordinator exists, but its enclosing native callers are not
            # installed here. Never acknowledge the whole personnel scope from
            # the three signing components alone.
            self.coverage["staffing"] = False
            return dict(self.coverage)
        except BaseException as error:
            # A guard may already have admitted a worker. Retain ownership and
            # identify failed setup until an idle cleanup retry can restore it.
            self.regulation_error = " ".join(str(error).split())[:512] or type(error).__name__
            self.stop_regulations()
            raise

    def install_current_regulations(self, generation, channel, *, native_rules, report):
        """Bind trusted setup to the career named in a previous bridge reply.

        Refresh before installing anything: a save reload or return to the menu
        must not attach a delayed setup request to a different career.
        The selector comes from the fixed adapter bundle, not a caller callback.
        """
        self.host.manager._main()
        if self.state != "loaded" or self.channel is not None:
            raise HookError("Regulation installation needs an unused loaded adapter bundle")
        state = self.host.career.current(generation)
        native = self.host.adapter_code.game_adapters.regulations.native_policy
        try:
            policy = native.NativeRegulationPolicy(state, channel)
        except Exception as error:
            report("REGULATIONS COMPATIBILITY FAILED rule selection: "
                   + " ".join(str(error).split())[:512]
                   + "; regulation setup skipped; continuing native game")
            return None  # The caller still owns the channel; no hooks were installed.
        # Keep a partially installed policy with its owned guards until busy
        # cleanup can finish; successful stop_regulations clears both together.
        self.policy = policy
        try:
            return self.install_regulations(state, channel, policy.select, native_rules=native_rules, report=report)
        except BaseException:
            if self.channel is None:
                self.policy = None
            raise

    def stop_regulations(self):
        """Restore the whole installation, or return False without changing it.

        Acquire the guard registries together before checking their active
        operations. Otherwise a worker could enter an earlier guard while a
        later one is being stopped, leaving a half-removed installation.
        """
        self.host.manager._main()
        with ExitStack() as locks:
            for hook in self.regulations:
                lock = getattr(hook, "_lock", None)
                if lock is not None:
                    if not lock.acquire(blocking=False):
                        return False
                    locks.callback(lock.release)
                    if hook._active:
                        return False
            for hook in reversed(self.regulations):
                hook.stop()
            if self.channel is not None:
                self.channel.close()
            self.regulations.clear()
            self.channel = None
            self.coverage.clear()
            self.regulation_error = None
            self.policy = None
        return True

    def load(self):
        """Import only bundled definitions, rolling back a failed load completely."""
        self.host.manager._main()
        if self.state == "closed":
            raise HookError("Adapter modules are closed")
        if self.state != "idle":
            return self.status()
        try:
            if (hasattr(self.host, "adapter_code") or any(
                    name == self.prefix or name.startswith(self.prefix + ".") for name in tuple(sys.modules))):
                raise HookError("Another module owns the private adapter namespace")
            root = ModuleType(self.prefix)
            root.__package__, root.__path__ = self.prefix, []
            root.__spec__ = importlib.util.spec_from_loader(self.prefix, loader=None, is_package=True)
            self.modules[self.prefix] = sys.modules[self.prefix] = root
            self.host.adapter_code = root
            # Adapter relative imports reuse the running infrastructure. Do not
            # create a second Manager class or a second callable registry.
            for name in ("manager", "callables"):
                alias = self.prefix + "." + name
                self.modules[alias] = sys.modules[alias] = self.host
                setattr(root, name, self.host)
            sys.meta_path.insert(0, self)
            for name in self.sources:
                importlib.import_module(self.prefix + "." + name)
            self.state = "loaded"
        except Exception as error:
            self._remove()
            self.state = "failed"
            self.error = " ".join(str(error).split())[:512] or type(error).__name__
        except BaseException:
            self._remove()
            raise
        finally:
            # Every bundled module is now loaded. Keep no persistent importer
            # that could intercept future game or third-party imports.
            if self in sys.meta_path:
                sys.meta_path.remove(self)
        return self.status()

    def find_spec(self, fullname, path=None, target=None):
        """Resolve our private names from the fixed bundle, never the filesystem."""
        if not fullname.startswith(self.prefix + "."):
            return None
        name = fullname[len(self.prefix) + 1:]
        if name not in self.sources:
            raise ModuleNotFoundError("Module is not in the trusted adapter bundle: " + name)
        return importlib.util.spec_from_loader(fullname, self, is_package=self.sources[name][2])

    def create_module(self, spec):
        return None  # Let Python initialise package/import metadata normally.

    def exec_module(self, module):
        name = module.__name__[len(self.prefix) + 1:]
        filename, source, package = self.sources[name]
        self.modules[module.__name__] = module
        exec(compile(source, filename, "exec"), module.__dict__)

    def _remove(self):
        for name, module in reversed(tuple(self.modules.items())):
            # Leave a replacement owned by somebody else intact.
            if sys.modules.get(name) is module:
                del sys.modules[name]
        root = self.modules.get(self.prefix)
        if root is not None and getattr(self.host, "adapter_code", None) is root:
            del self.host.adapter_code
        self.modules.clear()

    def close(self):
        """Stop owned adapters before removing definitions; defer busy workers."""
        if self.state == "closed":
            return True
        self.host.manager._main()
        if not self.stop_regulations():
            return False
        self._remove()
        self.state = "closed"
        return True
