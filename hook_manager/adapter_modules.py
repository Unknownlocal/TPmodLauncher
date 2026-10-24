# SPDX-License-Identifier: MPL-2.0
"""Load bundled game adapters as private modules without changing the game path."""

import importlib
import importlib.util
import sys
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

    def status(self):
        result = {"state": self.state, "modules": len(self.sources) if self.state == "loaded" else 0}
        if self.error is not None:
            result["detail"] = self.error
        return result

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
        """Remove definitions after the owner has stopped any adapter instances."""
        if self.state == "closed":
            return
        self.host.manager._main()
        self._remove()
        self.state = "closed"
