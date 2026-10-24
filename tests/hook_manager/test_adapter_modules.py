# SPDX-License-Identifier: MPL-2.0
"""Private adapter imports own their definitions, not game hooks or mod code."""

import sys
from types import ModuleType

import pytest

from hook_manager.adapter_modules import AdapterModules
from hook_manager.manager import HookError, Manager


@pytest.fixture
def host(monkeypatch):
    module = ModuleType("_owned_adapter_bundle")
    module.manager = Manager()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    yield module
    module.manager.stop()


def test_failed_import_rolls_back_package_aliases_and_temporary_finder(host):
    # The bundle cannot fall back to files for an undeclared private import.
    modules = AdapterModules(host, {
        "game_adapters": ("owned/__init__.py", "from .missing import value", True),
    })
    finders = tuple(sys.meta_path)
    result = modules.load()
    assert result["state"] == "failed" and "not in the trusted adapter bundle" in result["detail"]
    assert modules.modules == {} and not hasattr(host, "adapter_code")
    assert not any(name.startswith(modules.prefix) for name in sys.modules)
    assert tuple(sys.meta_path) == finders
    assert modules.load() == result  # A failed source set is not partly retried.
    modules.close()


def test_load_rejects_a_foreign_namespace_without_removing_it(host, monkeypatch):
    modules = AdapterModules(host, {})
    foreign = ModuleType(modules.prefix + ".foreign")
    monkeypatch.setitem(sys.modules, foreign.__name__, foreign)
    assert modules.load()["state"] == "failed"
    assert sys.modules[foreign.__name__] is foreign
    modules.close()


def test_close_preserves_replaced_modules_and_cannot_reopen(host, monkeypatch):
    modules = AdapterModules(host, {"game_adapters": ("owned/__init__.py", "value = 3", True)})
    assert modules.load()["state"] == "loaded"
    name = modules.prefix + ".game_adapters"
    replacement = ModuleType(name)
    # Restore deletion, not the old imported module, after this test ends.
    sys.modules.pop(name)
    monkeypatch.setitem(sys.modules, name, replacement)
    modules.close()
    modules.close()
    assert sys.modules[name] is replacement and not hasattr(host, "adapter_code")
    assert modules.status() == {"state": "closed", "modules": 0}
    with pytest.raises(HookError, match="closed"):
        modules.load()
