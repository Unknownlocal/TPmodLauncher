# Test architecture

The pytest suite follows the boundaries between the external launcher, trusted
in-game adapters and Python runner. It combines focused behaviour checks with
real Wasm execution, owned native helper processes and relocated player bundles.

## Test areas

| Area | Coverage |
| --- | --- |
| `launcher/` | Dependency verification, component admission, compilation cache, sandbox limits, pack loading, regulation definition admission, settings persistence and process/session lifetime; optional build and release validation. |
| `hook_manager/` | Deferred hooks, ownership/restoration, pack discovery, native menu/settings/theme adapters and the bridge. |
| `callables/` | Private callable targets, native refusal formats, shared wrapper ownership, validation, worker/shutdown coordination, guest access restrictions and dedicated native bridge acceptance. |
| `mod_runtime/` | Runner integrity and ABI, source snapshots, host-operation permissions, loading/callback budgets, cancellation, controller adoption and source-mod lifecycle. |
| `ci/` | Portable suite ownership, manual CI claims/statuses, completion gates and coverage combination/reporting. |

Small WAT components exercise signatures, permissions, traps, budgets and cleanup.
Python source examples execute through the trusted runner. Author-built Python
components have separate compatibility fixtures. Source-adapter checks reuse
the launcher's JSON and resource-ownership contracts; lower-level contract tests
cover their detailed validation rules.

Callable tests keep one representative per distinct validation or lifecycle
behaviour. Existing hook-manager tests own ordinary descriptor/import mechanics;
the callable native probe owns flat bootstrap and busy-shutdown restoration.
The real guest check owns private-module and endpoint absence; runtime tests own
generic source-load failure cleanup.

Preparation tests use synchronised worker gates to exercise queued/running
cancellation and abandoned results. Disposable compiler results isolate ownership
races when no guest is admitted; real compilation and controller-thread adoption
retain an integration check. Pack/session tests combine controlled process and
bridge events with real components to check ordering, progress, readiness,
replacement and cleanup.

## Fixtures and execution boundaries

The root `conftest.py` supplies temporary projects, synthetic dependency wheels,
runtime copies, a session-scoped runner and owned native menu/settings interfaces.
`launcher/conftest.py` adds local Git repositories, small Wasm components and
independent author-built Python component fixtures. Test fixtures own their
files and helper processes rather than using game data or live game injection.

Python integration fixtures share a session-local Wasmtime compilation cache.
Compiled Engine and Component code is reused; each runner receives a fresh Store,
guest state and grants. Tests passing an explicit cache retain that setup. Pytest cases execute
serially within each job, while separate CI jobs run independently. Background
preparation and Wasmtime's own compilation workers remain part of the tested
execution model.

Native integration uses real Frida sessions against owned CPython 3.13 helpers
that model imports, menus and process replacement. Embedded tests relocate a
prepared Windows bundle and invoke its entry point in subprocesses. These probes
check source-mod loading, selected-pack integration, dependency imports and
execution without developer build tools. Required acceptance modes fail when
their prerequisites are missing; local runs may skip unavailable platform cases.

## Test profiles

[Pytest configuration](../pyproject.toml) defines the selection boundaries:

| Marker | Boundary |
| --- | --- |
| `legacy` | Optional compatibility checks for author-built Python components. |
| `release` | Optional developer build, packaging and provenance checks. |
| `native` | Real Frida controller/bridge integration against owned processes. |
| `embedded` | Acceptance through the prepared Windows interpreter and bundle. |
| `frida_build` | Acceptance requiring a built reduced Frida wheel. |

Default pytest selection excludes `legacy` and `release`; explicit marker
expressions replace that default. Portable CI additionally excludes `native`,
`embedded` and `frida_build`. Windows acceptance selects its embedded/native
profiles separately and excludes `legacy` and `frida_build`. The ordinary checks
retain Python source mods and mods with prebuilt Wasm components.

## CI orchestration and trust

[`tests.yml`](../.github/workflows/tests.yml) is a manual entry point for an open
PR targeting `main`, including fork PRs. Workflow definitions come from the
trusted `main` workflow commit; jobs testing contributor code check out the
resolved PR head SHA. Push/PR events do not dispatch this suite automatically.
The separate base-branch instructions workflow supplies a neutral **Run Tests**
check linking to the dispatch form, without claiming an attempt or changing
`manual-ci`.

A trusted job atomically creates an annotated `ci-tested/<SHA>` tag to claim one
attempt for the commit. Duplicate requests and Actions re-runs are refused;
failures, cancellations and infrastructure interruptions retain the claim.
Marker retention underpins this policy. Only the original run can publish the
`manual-ci` result. Claim/status jobs use trusted code and write permissions;
PR-code jobs have read-only tokens and no inherited secrets. The tested revision
is the PR head, without a synthetic merge with `main`.

Each accepted run calls reusable launcher, hook-manager, private-callable, mod-runtime, CI-helper
and Windows acceptance workflows. Portable suites have separate Windows/Linux
jobs. [The selector](../.github/ci_tests.py) collects all portable modules to
validate exactly one directory-based owner, then executes the selected area.
Unknown or overlapping ownership and empty selected suites fail. Shared fixtures
and test-module imports still load across areas during collection.

## Shared build inputs

`dev-dependencies.yml` prepares a wheelhouse once per platform. Jobs install
those same-run wheels into separate environments without consulting a package
index. Pip downloads and built wheels are cached per PR, platform, Python version
and dependency inputs, with matching earlier caches available for restoration.

`runner-bundle.yml` builds and verifies one portable Python runner from the tested
commit. Launcher/runtime/callable jobs on both platforms and Windows bundle preparation
consume that artefact. The runner fixture verifies its receipt and compares the
guest, frozen policy/handler helpers and WIT bytes against the checkout. With
`TPM_TEST_RUNNER_BUNDLE` and `TPM_REQUIRE_SHARED_RUNNER=1`, missing or mismatched
inputs fail instead of triggering a rebuild. Local tests build once on demand
per pytest session when no shared bundle is supplied.

Regulation dispatch checks stay in the existing launcher and hook-manager suites.
They cover real Wasm binding, copied/bounded resident arguments, staged policy
publication, career reset and shared-runner failure, plus authenticated framing
and shutdown. One native check sends a fragmented shared action from the owned
CPython client to three mod owners in one shared Wasm Store under the same
deadline. Logical maximum framing capacity has a separate clock-controlled transport check; it
does not assert that arbitrary maximum-size actions complete within 100 ms.
It joins the existing native acceptance selection; no additional CI job or
per-test runner build is needed.

Windows acceptance prepares one verified player bundle for its independent
embedded and native jobs. The native job runs `tests/callables/` in a separate
named step with its own JUnit and coverage artefact, sharing interpreter and
bundle setup. Its general acceptance step excludes that directory. The manual reduced-Frida workflow reuses the runner
build workflow and keeps native builds, matching source exports and transport
acceptance together; release validation also checks the Windows candidate.

## Completion and coverage

The Windows runtime completion gate requires bundle preparation, both acceptance
jobs and aggregation of all three coverage fragments. The parent **Test suite complete** gate
requires the claim, dependencies, shared runner and all component/acceptance
workflows to succeed. A separate trusted job publishes that result as `manual-ci`.

Portable and Windows acceptance jobs retain JUnit results and raw coverage.
Portable jobs generate XML/HTML for their selected component; CI-helper coverage
measures `.github`. Callable coverage measures `hook_manager.callables`; the
parent hook-manager report measures its top-level modules. Windows acceptance combines its three required fragments,
rejecting missing or corrupt data. Coverage measures the pytest process;
isolated embedded children remain uninstrumented. `pyproject.toml` configures
line and branch measurement.

After a successful Test suite run, the reporting workflow downloads only that
run's XML and parses it without checking out or executing tested code. It expects
every declared portable suite on both platforms plus Windows acceptance. Suite/platform
percentages remain separate, and reports are never combined across runs or
commits. The summary distinguishes the workflow-definition commit from the
tested PR head recorded by the claim/status jobs.

Developer performance comparisons live in
[`benchmarks/regulations`](../benchmarks/regulations/README.md), outside CI.
They reuse one matching artefact per frozen source set and separate diagnostic
counts/memory from clean timings. CI retains independent handler arguments, exact accounting,
publication/reset and dense-catalog GC checks in the existing test cases; it does
not add a noisy benchmark matrix or per-scenario component builds.
