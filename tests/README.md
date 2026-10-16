# Test architecture

The pytest suite follows the boundaries between the external launcher, trusted
in-game adapters and Python runner. It combines focused behavior checks with
real Wasm execution, owned native helper processes and relocated player bundles.

## Test areas

| Area | Coverage |
| --- | --- |
| `launcher/` | Dependency verification, release/build tooling, component admission, compilation cache, sandbox limits, pack loading and process/session lifetime. |
| `hook_manager/` | Deferred hooks, ownership/restoration, pack discovery, native menu adaptation and the bridge. |
| `mod_runtime/` | Runner inventories and ABI, source snapshots, permissions, loading budgets, cancellation, controller adoption and source-mod lifecycle. |
| `ci/` | Exhaustive suite selection and complete, platform-separated coverage aggregation. |

Small WAT components exercise permissions, signatures, traps, budgets and cleanup.
Built Python components retain legacy contract coverage. Public schema 2 examples
run through the trusted runner, independently of the legacy manifest fixtures.
Source tests cover inert snapshots, portable layouts, file replacement and link
attempts, and bounded loading into real guest instances. Source-adapter tests
reuse the launcher JSON contract and diagnostic/menu ownership.

Preparation tests use synchronized worker gates to exercise queued and running
cancellation, abandoned results and controller-thread adoption. Pack/session
tests combine controlled process and bridge events with real components to check
load order, duplicate IDs, progress, readiness, replacements and cleanup.

## Fixtures and execution boundaries

The root `conftest.py` supplies temporary projects, synthetic dependency wheels,
runtime copies, a session-scoped trusted runner build and an owned native-menu
interface. `launcher/conftest.py` adds local Git source repositories, Wasmtime
components and independent legacy Python component fixtures. Fixtures keep game
files and external services outside test ownership.

Native integration uses real Frida sessions against owned CPython 3.13 helpers
that model imports, menus and process replacement. Embedded tests relocate a
prepared Windows bundle and invoke its entry point in subprocesses. The probes
check source-only loading, selected-pack integration, verified dependency imports
and execution without developer build tools.

The `native`, `embedded` and `frida_build` markers separate transport, packaged
Windows interpreter and reduced-build acceptance from portable tests. Required
acceptance modes fail when their prerequisites are missing. Filesystem cases
account for the volume's ability to represent case-colliding names; platform
specific skips are confined to the unsupported case.

## CI and coverage

Launcher, hook manager, mod runtime and CI helpers have independent workflows.
Each uses native path filters for pushes to `main` and pull requests, plus a
manual trigger. Filters include the area's code/tests and shared dependencies;
Markdown-only or unrelated changes create no test run. A reusable portable
workflow supplies separate Windows and Linux jobs without a selector runner.

Test ownership follows the `tests/launcher`, `tests/hook_manager`,
`tests/mod_runtime` and `tests/ci` directories. Each portable job collects all
portable modules to check ownership, then executes its selected area. Unknown
areas and overlapping ownership fail collection. The CI-helper workflow watches
all test changes so newly added test areas cannot bypass that check. Collection
still loads shared fixtures and test-module imports across areas.

Windows runtime acceptance has its own workflow and dependency filter. It
prepares one verified player bundle for independent embedded and native jobs.
Its completion check covers bundle preparation, both acceptance jobs and their
coverage aggregation. Component workflows succeed only when their Windows and
Linux jobs succeed; their results are separate from the runtime-only gate.
The reduced-Frida workflow remains manual and keeps matching builds, source
export and candidate transport acceptance together on Windows and Linux.

Every test job retains JUnit results and raw coverage. Portable jobs produce
XML/HTML for their selected component directly; CI-helper coverage measures
`.github`. Windows acceptance combines its two required fragments into one
report, rejecting incomplete or corrupt data. Reports are scoped to one workflow
run and are never assembled from different runs or previous commits.

`pyproject.toml` configures line/branch coverage. Isolated embedded children are
not instrumented. The separate reporting workflow downloads only the completed
run's reports, labels their workflow scope and parses XML without checking out
or executing tested code. Portable workflows report both platforms; Windows
runtime acceptance requires only its Windows report.
