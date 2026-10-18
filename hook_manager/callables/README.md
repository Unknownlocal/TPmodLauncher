# Private callable adapters

This package belongs to trusted launcher integrations. Trusted adapters provide
read-only projections; only copied plain values reach their configured dispatcher.
Mods cannot register native targets or import this host package.

| File | Responsibility |
| --- | --- |
| `target.py` | `CallableTarget`: approved native identity and decorator checks. |
| `result_contract.py` | `ResultContract`: native result structure, success selection and refusal rendering. |
| `binding.py` | `CallBinding`: trusted projection and target/phase association. |
| `adapters.py` | `CallableAdapters`: ownership, installation, dispatch, worker admission and cleanup. |
| `fingerprint.py` | Canonical native code fingerprints. |
| `__init__.py` | Private host exports; importing installs no hooks. |

Normal host imports use package-relative imports. The game bridge executes its
fixed `PYTHON_SOURCES` list in dependency order in the existing private namespace;
the implementation files skip package imports on that path. The package adds no
mod capability or registration operation. `Manager` continues to own normal
import observation and restoration journals.

The independent [`tests/callables/`](../../tests/callables/) suite contains unit,
real sandbox and native bridge acceptance tests. Its required CI workflow and
portable coverage reports are separate from the parent hook-manager suite.
Native acceptance uses owned helper processes through the real game bridge.
Shared root fixtures prepare the runner and owned native runtime.
