# SPDX-License-Identifier: MPL-2.0
"""Private trusted-adapter exports; importing installs no hooks.

Implementation files also support execution in dependency order in a shared
private namespace. Normal host imports use this package and relative imports.
Neither path supplies modules or registration operations to sandboxed mods.
"""

from .adapters import CallableAdapters
from .binding import CallBinding
from .signatures import method_group
from .result_contract import ResultContract
from .target import CallableTarget

__all__ = ["CallBinding", "CallableAdapters", "CallableTarget", "ResultContract", "method_group"]
