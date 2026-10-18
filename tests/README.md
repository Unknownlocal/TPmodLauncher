# Test architecture

The pytest suite follows the boundaries between the external launcher, trusted
in-game adapters and Python runner. It combines focused behavior checks with
real Wasm execution, owned native helper processes and relocated player bundles.

## Test areas

| Area | Coverage |
| --- | --- |
| `launcher/` | Dependency verification, release/build tooling, component admission, compilation cache, sandbox limits, pack loading, settings persistence and process/session lifetime. |
| `hook_manager/` | Deferred hooks, ownership/restoration, pack discovery, native menu/settings/theme adaptation and the bridge. |
| `mod_runtime/` | Runner inventories and ABI, source snapshots, permissions, loading budgets, cancellation, controller adoption and source-mod lifecycle. |
| `ci/` | Exhaustive portable suite selection and complete Windows acceptance coverage aggregation. |

Small WAT components exercise permissions, signatures, traps, budgets and cleanup.
Built Python components retain legacy contract coverage. Public Python source examples
run through the trusted runner, independently of the legacy manifest fixtures.
Source tests cover inert snapshots, portable layouts, file replacement and link
attempts, and bounded loading into real guest instances. Source-adapter tests
reuse the launcher JSON contract and owned native UI resources.

Author-built Python component checks carry the `legacy` marker and are excluded
from the required portable and Windows acceptance runs. Run them explicitly with
`python -m pytest tests -m "legacy and not embedded"`. The ordinary gate retains
prebuilt Wasm compatibility and the current source-mod path.

Preparation tests use synchronized worker gates to exercise queued and running
cancellation, abandoned results and controller-thread adoption. Pack/session
tests combine controlled process and bridge events with real components to check
load order, duplicate IDs, progress, readiness, replacements and cleanup.
Cancellation gates use disposable compiler results when no guest is admitted;
real native preparation and controller adoption retain an integration check.

## Fixtures and execution boundaries

The root `conftest.py` supplies temporary projects, synthetic dependency wheels,
runtime copies, a session-scoped trusted runner build and an owned native-menu
interface. `launcher/conftest.py` adds local Git source repositories, Wasmtime
components and independent legacy Python component fixtures. Fixtures keep game
files and external services outside test ownership.

Python integration tests share a session-local native compilation cache, as
normal launcher entry does. Engines, stores, guest state and grants remain fresh
for every instance. Tests supplying their own cache keep that explicit setup.
Source-adapter checks exercise representative contract failures; repeated JSON
and lifecycle variants belong to the shared contract rather than every layer.

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

`tests.yml` is the manual entry point. A maintainer with write access opens
**Actions > Test suite > Run workflow**, selects **main**, and supplies the open
PR number. This works for branches in this repository and in forks. PRs targeting
`main` require the `manual-ci` commit status; new commits need their own passing
result. Pushes and PR events do not automatically start the test suite.

PRs also receive a **Run Tests** informational check. Its Details
link opens the Test suite workflow page, and its summary gives the exact PR
number and instructions to select main and click Run workflow. This is a link
to GitHub's workflow form, not a button that dispatches tests from the PR.
`manual-ci-instructions.yml` runs only trusted base-branch code with check-write
permission, including for fork PRs. Its neutral check neither consumes a CI
attempt nor satisfies or changes the separate required `manual-ci` status.

The trusted workflow resolves the PR's exact head SHA, then atomically creates
an annotated `ci-tested/<SHA>` tag to claim its only CI attempt. The tag records
the PR, workflow commit and run ID. Protected marker tags cannot be updated or
deleted by collaborators. Duplicate requests and Actions re-runs are refused
before tests execute, without changing the original commit status. There can
still be a small rejected workflow run when someone requests a duplicate.
Failures, cancellations and infrastructure interruptions consume the attempt;
push a new commit to retry. The owner can bypass merge requirements. Marker tags
must be retained: deleting one through owner bypass allows that commit to run again.

Every accepted run calls the launcher, hook manager, mod runtime, CI-helper and
Windows acceptance workflows. Their definitions come from the trusted workflow
commit on `main`; every source checkout uses the resolved PR head SHA, including
dependency preparation and coverage combination. Jobs executing PR code have
read-only tokens and no inherited secrets. Only separate trusted jobs reserve
the commit and publish `manual-ci`, and only the original run can publish it.
The tests cover the PR head itself, rather than a synthetic merge with `main`.
Component workflows are reusable `workflow_call` definitions; the portable
workflow supplies separate Windows and Linux jobs for each area.

`dev-dependencies.yml` prepares a wheelhouse once per platform. Test and build
jobs download that run's wheels and install them into separate environments
without consulting a package index. Windows acceptance also shares one verified
player bundle between its embedded and native jobs.

Pip's download and built-wheel cache is preserved between commits on each PR,
separately for Windows/Linux and the Python version. Dependency changes create a
new cache key and can restore the PR's earlier cache. A first PR run can also
reuse the existing matching `setup-python` cache; new saves use a `pr-pip` key
so routine PR caching stays separate from release-build cache keys.

Test ownership follows the `tests/launcher`, `tests/hook_manager`,
`tests/mod_runtime` and `tests/ci` directories. Each portable job collects all
portable modules to check ownership, then executes its selected area. Unknown
areas and overlapping ownership fail collection. Collection still loads shared
fixtures and test-module imports across areas.

The Windows runtime completion check covers bundle preparation, both acceptance
jobs and their coverage aggregation. The parent `Test suite complete` check
requires dependency preparation and all component/acceptance workflows to
succeed. Each component requires both its Windows and Linux jobs.
The reduced-Frida workflow remains manual and keeps matching builds, source
export and candidate transport acceptance together on Windows and Linux.

Every test job retains JUnit results and raw coverage. Portable jobs produce
XML/HTML for their selected component directly; CI-helper coverage measures
`.github`. Windows acceptance combines its two required fragments into one
report, rejecting incomplete or corrupt data. Artifact names identify their
suite and platform within the shared run.

`pyproject.toml` configures line/branch coverage. Isolated embedded children are
not instrumented. After a successful `Test suite` run, the separate reporting
workflow downloads only that run's reports and parses XML without checking out
or executing tested code. It expects nine reports: four portable areas on both
platforms and Windows runtime acceptance. Percentages stay separate by suite and
platform; reports are never combined across runs or commits.
