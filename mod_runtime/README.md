# Mod runtime architecture

`mod_runtime` owns trusted Python-in-Wasm runner artifacts independently of game
process management and native hooks. Package, artifact and source-reader imports
use only standard-library code. The runtime module explicitly loads the sandbox;
the componentizer remains restricted to the developer build command.

`bundle.py` defines the private ABI, verifies the complete bounded artifact
inventory, and returns newly checked portable bytes for one `runner.wasm`.
Source and upstream notice files accompany the artifact. The receipt provides
integrity checks; publisher authentication comes from the distribution containing
that receipt.

`build.py` builds only the fixed trusted `guest.py` against the versioned
`runner.wit`, using pinned componentize-py in temporary staging. The runner
imports the complete typed host interface. The builder checks its ABI without
executing it and publishes into a fresh directory, with the receipt written last.

`contract.py` records each host operation's required capability and scalar
signature. `launcher/runner_host.py` uses this registry to bind a separate
operation table for each mod before sandbox instantiation. Granted operations
require trusted providers; ungranted operations receive rejection callbacks.
The existing sandbox enforces import signatures, execution limits and cleanup.
Permission denial disables the affected instance before its provider executes,
including calls made during component initialization or source loading.

`guest.py` provides an in-memory source importer and a separate author-facing
`wit_world` facade. Source loading and lifecycle calls form a private runtime
protocol. The launcher retains process/session ownership and sandbox admission;
`hook_manager` retains native integration.
Transferred source accumulates as chunks and is joined once before imports.
A guest audit hook declines reads of virtual source filenames so CPython can
report syntax errors from its tokenizer buffers without filesystem access.

`source.py` validates schema 2 source-only manifests and creates immutable,
ordered copies of UTF-8 Python files under `src/`. File, byte, entry, path and
depth limits bound preparation. Portable module names, explicit packages and
case checks prevent ambiguous inputs. `_source_files.py` pins ordinary ancestor
directories and rejects linked, reparse, shared and special-file inputs. POSIX
reads use directory-relative descriptors; Windows uses read-sharing handles to
prevent replacement during reads. Source remains inert text on the host.
`_source_policy.py` shares portable path rules and source byte/file limits
between the host and the frozen runner. The guest captures reserved module roots
from its own standard library and preloaded modules before author loading, then
rejects collisions during source transfer. Host snapshots do not decide runtime
module-name admission.

`runtime.py` owns a verified runner and one validated source snapshot through
transfer, import, explicit activation, string events and cleanup. A trusted
sandbox phase shares one fuel allowance, deadline and host-call count across
transfer and import; UTF-8 chunks and aggregate byte/file/call bounds limit input
work. Normal activation/event budgets resume afterward. Optional compiler
results are adopted only after their portable bytes match the verified bundle.
Cleanup ownership begins before admission and follows the sandbox until closure.
Its read-only sandbox property exposes the execution boundary to trusted adapters.
`launcher/source_mod.py` applies the launcher's shared mod lifecycle and bounded
JSON event contract to this runner. Both mod paths use the same host-operation
factory for diagnostic identity, printable messages and menu ownership; the
runner's own binding registry continues to enforce source-mod permissions.

Player discovery, responsive preparation and startup integration are separate
implementation stages. The current component-loading path retains its existing
manifest format and permission admission rules.
