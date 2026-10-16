# Mod runtime architecture

`mod_runtime` owns trusted Python-in-Wasm runner artifacts independently of game
process management and native hooks. Package, artifact and source-reader imports
use only standard-library code. Runtime and preparation modules explicitly load
the sandbox/compiler; the componentizer remains restricted to the developer build
command.

`bundle.py` defines the private ABI, verifies the complete bounded artifact
inventory, and returns newly checked portable bytes for one `runner.wasm`.
The installed bundle lives under `libs/mod-runners`; source and upstream notice
files accompany the artifact. Normal launcher entry verifies it during both
`--check-only` and game launch before passing it to pack loading. The receipt
provides integrity checks; publisher authentication comes from the distribution
containing that receipt.

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
`wit_world` facade exposing the author base class, diagnostics and menu operations.
The frozen guest includes `json`, `math`, `os` and their runtime dependencies;
mods supply Python source, without generated bindings or third-party packages.
Source loading and lifecycle calls form a private runtime protocol. The launcher
retains process/session ownership and sandbox admission; `hook_manager` retains
native integration.
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
work. Interpreter initialization has 20 million fuel units and five seconds;
source transfer/import shares 256 million fuel units and five seconds. Activation
and each event have five million fuel units and one second. The sandbox allows
64 host calls per phase and 64 MiB guest linear memory per instance. One built
runner artifact serves separate instances and Python state for individual mods.
Optional compiler results are adopted only after their portable bytes match the
verified bundle.
Cleanup ownership begins before admission and follows the sandbox until closure.
Its read-only sandbox property exposes the execution boundary to trusted adapters.
`launcher/source_mod.py` applies the launcher's shared mod lifecycle and bounded
JSON event contract to this runner. Both mod paths use the same host-operation
factory for diagnostic identity, printable messages and menu ownership; the
runner's own binding registry continues to enforce source-mod permissions.

`preparation.py` runs bounded source snapshotting, runner-byte verification and
native compilation on a caller-owned thread executor. Each job owns a future and
cancellation signal; nonblocking `take()` transfers a completed snapshot,
compiler result and worker-local timings to the controller once. `close()` cancels
queued work or disposes an abandoned result when running work returns. Reads and
native compilation are not forcibly interrupted. The controller retains executor
lifetime and admission bounds; workers create no Store or game registrations.
Bundle bootstrap and source-mod admission retain their own integrity reads.
An optional admitted manifest is compared with the worker snapshot before native
compilation, preventing identity or permission changes during preparation.

Normal launcher sessions in `launcher/pack_mods.py` use the pinned manifest reader
to dispatch supported schemas and reject duplicate IDs before guest execution.
They load schema 2, API 0.2.0 Python sources through SourcePreparation/SourceMod
and retain explicit schema 1 Wasm support. Source manifests name a dotted entry
module under `src/`; authors supply the manifest and source. Python components
in schema 1 are rejected in this mode. Internal component-only sessions retain
the original Manifest.read admission behavior. Preparation jobs belong to the
session's loading generator; closing it cancels or disposes unfinished work.
Activation, progress, native menu readiness and cleanup use the existing
controller and bridge protocols.

Bootstrap registers this package alongside launcher and hook_manager without
adding game directories to Python's search path. The release workflow builds the
fixed runner with the pinned developer componentizer. Release preparation verifies
and copies the complete runner bundle, trusted package sources and upstream
notices into staging. The player loads that prebuilt runner without a componentizer
or per-mod Wasm build. The runner's exact inventory remains separate from the
bundled Python dependency inventory.
