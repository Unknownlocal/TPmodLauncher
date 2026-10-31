# Private callable adapters

This package owns wrappers around native game methods for trusted launcher
adapters. Mods cannot register or call native targets through this package.

| File | Responsibility |
| --- | --- |
| `target.py` | `CallableTarget`: approved native paths, signatures and wrapper ownership checks. |
| `binding.py` | `CallBinding`: connects a target to trusted input-copying and refusal functions. |
| `refusals.py` | Native failure formats: `False` or `(False, message)`. |
| `adapters.py` | `CallableAdapters`: installs shared wrappers, tracks owners, admits workers and restores native methods. |
| `signatures.py` | Checks signatures and retains native helper functions. |
| `__init__.py` | Private exports; importing installs no hooks. |

A trusted subclass supplies validation. `RegulationGuards` prepares one copied
proposal and checks every subscribed mod through the shared regulation channel.
All owners of a native target must use the same refusal function. After an
allowance, native code runs once and keeps its own return value or exception.
A failed validator stays unavailable until its owner is explicitly removed.

`method_group` checks a trusted mapping of helper names to signatures and retains
the current functions, including whether they are static methods. Compatibility
checks use paths and signatures, including defaults and annotations; changed
method bodies do not disable integration. Native decorators still run when
validation allows execution. The owning adapter reports and skips incompatible
integration; mods cannot approve targets or provide these helper mappings.

Normal imports use this package. The game bridge executes a fixed source list in
dependency order in its private namespace. `Manager` owns deferred imports and
the record of assignments needed to restore native methods. A trusted native
wrapper grants worker access with an `operation()` block; leaving it removes that
access, even after an error. Nested blocks are rejected. Active scopes and calls
prevent configuration changes or cleanup until their work finishes.

The separate [`tests/callables/`](../../tests/callables/) suite covers native
wrapper behaviour, shared ownership, worker safety, guest access restrictions
and real bridge cleanup using owned helper processes. Its CI workflow reuses the
shared runner and native runtime artefacts.
