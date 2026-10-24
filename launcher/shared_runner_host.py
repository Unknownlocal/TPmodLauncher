# SPDX-License-Identifier: MPL-2.0
"""Route the fixed runner host operations through an admitted group's mod IDs."""

from functools import partial

from mod_runtime.contract import RUNNER_HOST_OPERATIONS
from launcher.runner_host import bind_owner_operations
from launcher.sandbox import Operation, SandboxError


def bind_shared_operations(capabilities, providers):
    """Snapshot per-owner providers; source IDs cannot escape this group.

    Authors share Python objects and may borrow a peer's facade. This routing
    preserves the facade's owner and ordinary manifest checks; it does not restore
    mutual isolation or claim that peer capabilities are a security boundary.
    """
    providers = dict(providers)
    if providers.keys() != capabilities.keys():
        raise SandboxError("Shared providers must match the admitted source owners")
    tables = {owner: bind_owner_operations(grants, providers[owner]) for owner, grants in capabilities.items()}
    operations = {}

    def route(name, owner, *arguments):
        table = tables.get(owner)
        if table is None:
            raise SandboxError("Host call names a source outside this runtime group")
        return table[name].callback(*arguments)

    for name, spec in RUNNER_HOST_OPERATIONS.items():
        operations[name] = Operation(spec.parameters, spec.result,
                                     partial(route, name.removeprefix("source-")))
    return operations
