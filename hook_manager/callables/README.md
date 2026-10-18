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

Normal host imports use package-relative imports. Importing the package installs
no hooks. It adds no mod capability or registration operation. `Manager`
continues to own normal import observation and restoration journals.

The independent [`tests/callables/`](../../tests/callables/) suite contains unit
and real sandbox isolation checks. Its required CI workflow and portable coverage
reports are separate from the parent hook-manager suite. Shared root fixtures
prepare the runner.
