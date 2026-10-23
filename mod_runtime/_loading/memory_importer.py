# SPDX-License-Identifier: MPL-2.0
"""Import author modules from source already transferred into guest memory."""

import importlib.abc
import importlib.util


class MemoryImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Resolve ordinary modules and explicit packages from one source table."""

    def __init__(self, sources):
        self.sources = sources

    def find_spec(self, fullname, path=None, target=None):
        stem = fullname.replace(".", "/")
        package = stem + "/__init__.py"
        module = stem + ".py"
        if package in self.sources and module in self.sources:
            raise ImportError("Ambiguous module and package")
        if package in self.sources:
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        if module in self.sources:
            return importlib.util.spec_from_loader(fullname, self, is_package=False)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        stem = module.__name__.replace(".", "/")
        path = stem + ("/__init__.py" if module.__spec__.submodule_search_locations is not None else ".py")
        module.__file__ = "<mod>/" + path
        exec(compile(self.sources[path], module.__file__, "exec"), module.__dict__)
