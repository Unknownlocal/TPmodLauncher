# SPDX-License-Identifier: MPL-2.0
"""Read the game's built-in category defaults before a career exists."""

import importlib
import json
from pathlib import Path
import sys
from types import ModuleType

from ...callables import CallableTarget
from ...manager import HookError


def read_regulation_defaults(folder, *, wait_for_imports=None):
    """Copy the defaults used by native loading, without constructing game state.

    This fixed trusted operation supplies reference data for mod preparation.
    File-defined categories still take precedence in the launcher and the game.
    Return None with the exact imports that can wake this parked request. The
    observer rechecks completion, including imports finishing before it attaches.
    """
    module = "managers.regulation_manager"
    if module not in sys.modules:
        # A bridge pulse can interrupt any import, including this module's
        # indirect dependencies. Wait until imports finish rather than re-enter
        # a partly defined module. This scan runs only before the fixed import.
        constants = sys.modules.get("constants")
        pending = {name for name, imported in tuple(sys.modules.items())
                   if type(imported) is ModuleType
                   and getattr(vars(imported).get("__spec__"), "_initializing", False)}
        if constants is None:
            pending.add("constants")
        if pending:
            if wait_for_imports is not None:
                wait_for_imports(pending)
            return None
        # The fixed native module defines data/helpers, not careers or engines.
        importlib.import_module(module)
    loader = CallableTarget("regulations.defaults.loader", module, ("RegulationManager",),
                            "_load_regulation_defs", "(self) -> 'Dict[str, Dict[str, Any]]'")
    if loader.resolve({}) is None:
        if wait_for_imports is not None:
            wait_for_imports({module})
        return None  # Another normal import may still be finishing this module.
    values = vars(sys.modules[module])
    targets = (
        CallableTarget("regulations.defaults.tyres", module, ("TyreModel",), "load",
                       "(cls, path: 'str') -> \"'TyreModel'\""),
        CallableTarget("regulations.defaults.compounds", module, (), "regulation_for_model", "(model)"),
    )
    for target in targets:
        if target.resolve({}) is None:
            raise HookError("Regulation defaults need the loaded native tyre helpers")
    # The compound default depends only on mandatory_compound_count. Calling
    # load directly uses the same pack file/defaults without career wear effects.
    model = values["TyreModel"].load(str(Path(folder) / "tyremodel.json"))
    categories = [*values["ENTRY_REGULATIONS"], values["CAR_PROFILE_REGULATION"], values["CALENDAR_REGULATION"],
                  values["regulation_for_model"](model), *values["FORMULA_FUEL_REGULATIONS"], *values["BONUS_REGULATIONS"]]
    # Detach the data once at startup. No live game objects cross the bridge,
    # and ordinary validation never serialises this catalogue again.
    encoded = json.dumps(categories, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(categories) > 128 or len(encoded.encode("utf-8")) > 1024 * 1024:
        raise HookError("Native regulation defaults exceed their category/data limits")
    return json.loads(encoded)
