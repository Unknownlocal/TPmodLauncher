# SPDX-License-Identifier: MPL-2.0
"""Bind the full trusted-runner interface using one mod's fixed permission set."""

from functools import partial

from mod_runtime.contract import HOST_OPERATIONS, SOURCE_HOST_OPERATIONS
from launcher.sandbox import Operation, SandboxError


def _deny(name, capability, *arguments):
    raise SandboxError(f"Capability {capability!r} is required for {name!r}")


def _unowned(name, *arguments):
    raise SandboxError(f"An admitted source owner is required for {name!r}")


def bind_operations(capabilities, providers):
    """Snapshot trusted grants/providers; bind denied operations before instantiation.

    Providers are bounded host callbacks keyed by operation name, supplied by the
    launcher rather than mod code. Missing granted providers are loading errors.
    The existing sandbox wraps every callback with value/budget/error handling.
    """
    if type(capabilities) not in (set, frozenset) or any(type(name) is not str for name in capabilities):
        raise SandboxError("Expected an explicit capability set")
    capabilities = frozenset(capabilities)
    # The private regulation adapter uses runner exports, never extra host calls.
    # Public manifest versions still cannot request this planned capability.
    if capabilities - {spec.capability for spec in HOST_OPERATIONS.values()} - {"regulations"}:
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
    # An individual runner has no shared owner table. Never interpret a caller-
    # supplied ID as authority to reach another mod's providers through this path.
    for name, spec in SOURCE_HOST_OPERATIONS.items():
        operations[name] = Operation(spec.parameters, spec.result, partial(_unowned, name))
    return operations
