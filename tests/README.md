# Test architecture

The pytest suite mirrors the production split between the external launcher and
the in-game hook manager. `launcher/` contains dependency, packaging, process
lifecycle, component, cache and sandbox tests. `hook_manager/` contains manager,
pack discovery, native menu and bridge tests.

`mod_runtime/` checks trusted runner builds, private ABI compatibility, complete
bundle inventories, per-mod permission enforcement and relocated bundled-interpreter
execution without compiler packages. Build fixtures are separate from player
execution, and the existing production sandbox supplies execution limits.
Source-input checks cover schema validation, immutable snapshots, package
layouts, resource limits, ordinary-file reads and replacement/link attempts on
Windows and POSIX. The embedded probe snapshots source before guest execution.
Lifecycle tests exercise UTF-8 transfer, large inputs, shared source budgets,
prepared-component ownership and cleanup across loading/callback failures.
Sandbox phase tests verify aggregate fuel/deadlines/host calls and restoration
of normal callback limits. The bundled-interpreter probe uses the same loader.
Source-adapter checks reuse the launcher's JSON contract and exercise identity,
menu rollback, denied operations and admission cleanup through real source mods.
The bundled-interpreter probe also exercises this contract adapter.
Preparation tests use real compilation with synchronized worker gates to exercise
queued/running cancellation, late disposal, thread ownership and immutable source
adoption. The bundled-interpreter probe prepares on a worker before controller
activation and event delivery.
Source-pack tests cover schema admission, metadata changes, mixed-profile duplicate
IDs, no author-Wasm fallback, progress and native registration. Controller tests
exercise source ownership through exits, exec/PID replacement and pending worker
cancellation. The relocated probe uses real selected-pack loading and trusted
package bootstrap without build tools.

The root `conftest.py` supplies temporary projects, synthetic dependency wheels,
runtime copies, a shared trusted runner build and a small owned native-menu
interface. `launcher/conftest.py` adds real Git source repositories, Wasmtime
components and independent legacy Python component fixtures built once per
session. Public schema 2 examples load through the trusted runner. Embedded entry
checks use the installed runner inventory and relocated sources without developer
tools. Fixtures stay separate from game files and external services.

Small WAT components exercise admission, permissions, limits, callback failures
and cleanup. Built Python components exercise generated WIT bindings, private
guest state, denied host I/O and the retained legacy component builder. Cache tests combine real
Wasmtime compilation with filesystem and platform access checks. Pack-loading
tests combine manifests and components with controlled process and bridge events
to exercise ordering, cancellation, readiness and per-session ownership.

Native integration tests run the real Frida controller and trusted bridge
against owned CPython 3.13 helper processes. Their targets model module imports,
menu interfaces and process replacement. Embedded-runtime tests relocate a
prepared Windows bundle and invoke its production entry point in subprocesses,
covering dependency verification, import isolation and component execution.

The `native`, `embedded` and `frida_build` markers identify tests requiring native
transport, the packaged Windows interpreter or a reduced Frida build. Portable
jobs cover Windows, Linux and macOS; native acceptance uses the supported
Windows and Linux transports. Required acceptance modes turn missing native
prerequisites into failures.

`pyproject.toml` defines collection, markers and line/branch coverage for
`launcher`, `hook_manager` and `mod_runtime`. The pytest workflow runs four portable
suites independently on Windows, Linux, macOS Intel and macOS ARM: host infrastructure
and tooling, runner internals, mod API, and pack/session integration. The CI selector
requires every portable test module to belong to exactly one suite; new unassigned
modules and overlapping ownership fail collection. CI helper tests live in `ci/`.

A separate Windows job prepares one verified player bundle for independent embedded
and native acceptance jobs. Each test job retains JUnit results and raw coverage.
An unprivileged job combines complete coverage fragments separately for each
platform; missing or corrupt fragments fail collection. The `Tests complete` job
requires every test, bundle and coverage job to succeed. The reporting workflow
reads the resulting XML reports without checking out or executing tested code.
The reduced-Frida workflow keeps candidate builds, source export and transport
acceptance together, while portable behavior is covered by the pytest workflow.
