# SPDX-License-Identifier: MPL-2.0
"""Install the small wit_world API used by Python source mods."""

import sys
from types import ModuleType

import runner_bindings as _bindings


class AuthorWorld:
    """Author base class; private source/regulation exports are kept separate."""

    def activate(self):
        raise NotImplementedError

    def on_event(self, name, payload):
        raise NotImplementedError


def install_author_api():
    """Expose the author class and existing host operations under wit_world."""
    api = ModuleType("wit_world")
    api.WitWorld = AuthorWorld
    for name in ("report", "add_menu_button", "register_settings", "save_settings", "register_theme"):
        setattr(api, name, getattr(_bindings, name))
    sys.modules["wit_world"] = api
