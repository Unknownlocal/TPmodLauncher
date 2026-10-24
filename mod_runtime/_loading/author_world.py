# SPDX-License-Identifier: MPL-2.0
"""Install the small wit_world API used by Python source mods."""

import sys
from functools import partial
from types import ModuleType

import runner_bindings as _bindings


class AuthorWorld:
    """Author base class; private source/regulation exports are kept separate."""

    def activate(self):
        raise NotImplementedError

    def on_event(self, name, payload):
        raise NotImplementedError


def source_author_api(owner, import_mod):
    """Bind host calls to the module's mod, including when peers call its functions."""
    api = ModuleType("wit_world")
    api.WitWorld = AuthorWorld
    api.import_mod = import_mod
    for name in ("report", "add_menu_button", "register_settings", "save_settings", "register_theme"):
        setattr(api, name, partial(getattr(_bindings, "source_" + name), owner))
    return api


def install_author_api():
    """Reserve the common module; each source module gets its own bound host API."""
    api = ModuleType("wit_world")
    api.WitWorld = AuthorWorld
    sys.modules["wit_world"] = api
