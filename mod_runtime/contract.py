# SPDX-License-Identifier: MPL-2.0
"""Typed host interface of the trusted runner; permissions belong to each mod."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class HostOperation:
    """An operation's required permission and scalar WIT signature."""

    capability: str
    parameters: tuple[str, ...]
    result: str | None = None


HOST_OPERATIONS = MappingProxyType({
    "report": HostOperation("diagnostics", ("string",)),
    "add-menu-button": HostOperation("main-menu", ("string", "string", "string")),
    "register-settings": HostOperation("ui-settings", ("string",), "string"),
    "save-settings": HostOperation("ui-settings", ("string",), "string"),
    "register-theme": HostOperation("ui-settings", ("string",)),
})

# Shared calls carry an admitted source ID. The ordinary author methods still
# take the same arguments; their per-mod facade supplies this private field.
SOURCE_HOST_OPERATIONS = MappingProxyType({
    "source-" + name: HostOperation(spec.capability, ("string", *spec.parameters), spec.result)
    for name, spec in HOST_OPERATIONS.items()
})
RUNNER_HOST_OPERATIONS = MappingProxyType({**HOST_OPERATIONS, **SOURCE_HOST_OPERATIONS})

# Source manifests select an exact public version, independently of the runner ABI.
SOURCE_CAPABILITIES = MappingProxyType({
    "0.2.0": frozenset({"diagnostics", "main-menu"}),
    "0.3.0": frozenset(spec.capability for spec in HOST_OPERATIONS.values()),
})
