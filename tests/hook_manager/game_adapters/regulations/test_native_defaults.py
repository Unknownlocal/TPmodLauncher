# SPDX-License-Identifier: MPL-2.0
"""Read only native fallback data, using the selected pack's tyre-model helper."""

import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.game_adapters.regulations.regulation_native_defaults import read_regulation_defaults
from hook_manager.manager import HookError


@pytest.fixture
def native(monkeypatch):
    module = ModuleType("managers.regulation_manager")
    module.RegulationManager = type("RegulationManager", (), {"_load_regulation_defs": lambda self: None})
    module.RegulationManager._load_regulation_defs.__annotations__ = {"return": "Dict[str, Dict[str, Any]]"}
    calls = []
    class TyreModel:
        @classmethod
        def load(cls, path: "str") -> "'TyreModel'":
            calls.append(path)
            return SimpleNamespace(mandatory_compound_count=1)
    module.TyreModel = TyreModel
    module.regulation_for_model = lambda model: {"key": "compound", "default": model.mandatory_compound_count}
    module.ENTRY_REGULATIONS = [{"key": "entry", "options": [{"key": "open"}]}]
    module.CAR_PROFILE_REGULATION = {"key": "profile"}
    module.CALENDAR_REGULATION = {"key": "calendar"}
    module.FORMULA_FUEL_REGULATIONS = [{"key": "fuel"}]
    module.BONUS_REGULATIONS = [{"key": "points"}]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module, calls


def test_defaults_use_native_catalogue_and_pack_tyre_helper_and_return_independent_json(native, tmp_path):
    module, calls = native
    result = read_regulation_defaults(tmp_path)
    assert calls == [str(tmp_path / "tyremodel.json")]
    assert [row["key"] for row in result] == ["entry", "profile", "calendar", "compound", "fuel", "points"]
    assert result[3]["default"] == 1
    result[0]["options"].clear()
    assert module.ENTRY_REGULATIONS[0]["options"] == [{"key": "open"}]


def test_incompatible_native_signature_is_rejected_before_reading_pack_data(native, tmp_path, monkeypatch):
    module, calls = native
    monkeypatch.setattr(module.RegulationManager, "_load_regulation_defs", lambda self, changed: None)
    with pytest.raises(HookError, match="signature"):
        read_regulation_defaults(tmp_path)
    assert calls == []


def test_waits_for_unfinished_imports_before_loading_fixed_native_definitions(native, tmp_path, monkeypatch):
    module, calls = native
    monkeypatch.delitem(sys.modules, module.__name__)
    constants = ModuleType("constants")
    constants.__spec__ = SimpleNamespace(_initializing=True)
    monkeypatch.setitem(sys.modules, "constants", constants)
    imports = []
    def importing(name):
        assert name == module.__name__
        imports.append(name)
        monkeypatch.setitem(sys.modules, name, module)
        return module
    monkeypatch.setattr(importlib, "import_module", importing)
    assert read_regulation_defaults(tmp_path) is None and not imports and not calls
    constants.__spec__._initializing = False
    dependency = ModuleType("managers.tyre_model")
    dependency.__spec__ = SimpleNamespace(_initializing=True)
    monkeypatch.setitem(sys.modules, dependency.__name__, dependency)
    assert read_regulation_defaults(tmp_path) is None and not imports and not calls
    dependency.__spec__._initializing = False
    assert read_regulation_defaults(tmp_path)[0]["key"] == "entry"
    assert imports == [module.__name__] and len(calls) == 1


def test_oversized_native_defaults_are_not_transferred(native, tmp_path):
    module, _ = native
    module.CAR_PROFILE_REGULATION["oversized"] = "x" * (1024 * 1024)
    with pytest.raises(HookError, match="data limits"):
        read_regulation_defaults(tmp_path)
