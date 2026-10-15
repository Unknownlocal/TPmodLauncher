# SPDX-License-Identifier: MPL-2.0
"""Bind the full trusted-runner interface using one mod's fixed permission set."""

from functools import partial

from mod_runtime.contract import HOST_OPERATIONS
from launcher.sandbox import Operation, SandboxError


def _deny(name, capability, *arguments):
    raise SandboxError(f"Capability {capability!r} is required for {name!r}")


def bind_operations(capabilities, providers):
    """Snapshot trusted grants/providers; bind denied operations before instantiation.

    Providers are bounded host callbacks keyed by operation name, supplied by the
    launcher rather than mod code. Missing granted providers are loading errors.
    The existing sandbox wraps every callback with value/budget/error handling.
    """
    if type(capabilities) not in (set, frozenset) or any(type(name) is not str for name in capabilities):
        raise SandboxError("Expected an explicit capability set")
    capabilities = frozenset(capabilities)
    if capabilities - {spec.capability for spec in HOST_OPERATIONS.values()}:
        raise SandboxError("Unsupported runner capabilities")
    providers = dict(providers)
    if providers.keys() - HOST_OPERATIONS.keys():
        raise SandboxError("Unknown runner host provider")
    operations = {}
    for name, spec in HOST_OPERATIONS.items():
        if spec.capability in capabilities:
            callback = providers.get(name)
            if not callable(callback):
                raise SandboxError(f"Granted operation {name!r} requires a trusted provider")
        else:
            callback = partial(_deny, name, spec.capability)
        operations[name] = Operation(spec.parameters, spec.result, callback)
    return operations
