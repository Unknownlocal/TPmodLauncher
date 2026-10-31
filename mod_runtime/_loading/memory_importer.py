# SPDX-License-Identifier: MPL-2.0
"""Import author modules from source already transferred into guest memory."""

import importlib.abc
import importlib.util


class MemoryImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Find a module's owning mod, then import its stored source."""

    def __init__(self, groups):
        self.groups = groups

    def resolve(self, fullname):
        # Group packages already exist. Handle only source below a mod's prefix;
        # leave standard-library and other imports to Python's normal finders.
        parts = fullname.split(".", 2)
        if len(parts) != 3:
            return None
        namespace = ".".join(parts[:2])
        member = self.groups.get(namespace)
        if member is None:
            return None
        sources, imports = member
        return sources, parts[2].replace(".", "/"), imports, namespace

    def find_spec(self, fullname, path=None, target=None):
        resolved = self.resolve(fullname)
        if resolved is None:
            return None
        sources, stem, _, _ = resolved
        package = stem + "/__init__.py"
        module = stem + ".py"
        if package in sources and module in sources:
            raise ImportError("Ambiguous module and package")
        if package in sources:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        if module in sources:
            return importlib.util.spec_from_loader(fullname, self, is_package=False)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        sources, stem, imports, namespace = self.resolve(module.__name__)
        path = stem + ("/__init__.py" if module.__spec__.submodule_search_locations is not None else ".py")
        module.__file__ = "<mod>/" + namespace + "/" + path
        # Each owner's modules share one builtins table with a local import
        # resolver. Never replace Python's process-wide import function.
        module.__dict__["__builtins__"] = imports.builtins
        exec(compile(sources[path], module.__file__, "exec"), module.__dict__)
