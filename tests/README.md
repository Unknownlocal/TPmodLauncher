# Test architecture

Component tests follow the launcher, trusted game adapters and Python runner.
Integration suites check features that need several of those packages together.
The tests use small Wasm components, Python source mods, owned native helper
processes and relocated player bundles. Fixtures own their files and processes; they do
not use live game data or inject into a running game.

## Test areas

| Area | Coverage |
| --- | --- |
| `launcher/` | Dependency and component admission, compilation reuse, sandbox limits, pack loading, regulation definitions and dispatch, settings and process lifetime. |
| `hook_manager/` | Hooks and restoration, pack discovery, native UI adapters, career loading and game validation. Child adapter suites run separately. |
| `callables/` | Private targets, refusal results, wrapper ownership, worker coordination, guest access restrictions and native bridge acceptance. |
| `mod_runtime/` | Runner integrity, source snapshots and shared loading, permissions, budgets, cancellation and source-mod lifetime. |
| `ci/` | Suite selection, CI claims and completion gates, coverage aggregation and reporting. |
| `integration/regulations/` | Real mod execution, shared rule data, native guards and regulation transport working together. |
| `integration/source_loading/` | Source-pack admission, runner execution, UI registration and startup readiness. |
| `integration/session_lifetime/` | Controller sessions, private bridge loading and cleanup across game-process changes. |

Small Wasm source fixtures (WAT) check signatures, permissions, traps and cleanup.
Python source examples run through the trusted Wasm runner; author-built Python
components have separate compatibility fixtures. Regulation checks cover fresh
handler arguments, exact size accounting, publication and reset, cleanup of large
cached catalogues, shared-runner failure and authenticated transport. A native check
sends one shared proposal to three mod owners under the same deadline. Maximum
frame-size checks do not promise that arbitrary workloads finish within 100 ms.

## Fixtures and execution boundaries

The root `conftest.py` supplies temporary projects, synthetic dependency wheels,
runtime copies, one session runner and owned native menu/settings interfaces.
`source_fixtures.py` shares Python source and small Wasm component fixtures.
`launcher/conftest.py` adds local Git repositories and author-component build fixtures.
`regulation_support.py` supplies shared rule data and real source-group factories.
Preparation tests use worker gates for cancellation races; stand-in compiler
results avoid compilation where no guest runs. Real compilation and controller-thread
adoption retain an integration check.

Python tests share compiled Engine and Component code through a session-local
cache. Each runner gets a fresh Store, guest state and grants; explicit cache
fixtures retain their own setup. Tests run one at a time within each job, while CI
jobs run independently. Background preparation and Wasmtime compilation workers
remain part of the tested execution model.

Native tests use Frida against owned CPython 3.13 helpers. Embedded tests invoke
relocated Windows bundles to check startup and execution without developer build
tools. Required acceptance modes fail if prerequisites are absent; local runs
may skip unavailable platform cases.

## Test profiles

[Pytest configuration](../pyproject.toml) defines these profiles:

| Marker | Boundary |
| --- | --- |
| `legacy` | Optional compatibility checks for author-built Python components. |
| `release` | Optional developer build, packaging and provenance checks. |
| `native` | Frida integration against owned processes. |
| `embedded` | Acceptance through the prepared Windows interpreter and bundle. |
| `frida_build` | Acceptance requiring a built reduced Frida wheel. |

Default pytest selection excludes `legacy` and `release`; explicit marker
expressions replace that default. Portable CI also excludes `native`, `embedded`
and `frida_build`. Windows acceptance selects embedded/native tests separately,
excluding `legacy` and `frida_build`. Ordinary checks cover Python source mods
and mods with prebuilt Wasm components.

## CI ownership and shared inputs

[`tests.yml`](../.github/workflows/tests.yml) runs manually for open PRs targeting
`main`, including forks. Trusted workflow code resolves and tests the exact PR
head without a synthetic merge. The instructions workflow supplies a neutral
**Run Tests** link; it neither claims an attempt nor changes `manual-ci`.

The claim job atomically creates `ci-tested/<SHA>`. Duplicate requests and reruns
are refused; failures and cancellations keep the claim. Only the original run
can publish `manual-ci`. Claim/status jobs use trusted code and write permissions;
PR-code jobs have read-only tokens and no inherited secrets.

Reusable workflows run portable areas on Windows/Linux and acceptance on Windows.
[The selector](../.github/ci_tests.py) collects all portable modules, requires one
directory-based owner per module and rejects unknown, overlapping or empty suites.
One table defines test paths, coverage sources, parent suites and shared-runner
requirements. Parent suites exclude their declared children. The game-adapter
parent covers generic native support; personnel, regulations and projects have
their own child suites. Integration features have a reusable parent workflow
and one child workflow per populated feature folder.
Shared fixtures and test imports still load across areas during collection.

`dev-dependencies.yml` prepares one wheelhouse per platform. Jobs install those
same-run wheels into separate environments without a package index. Build caches
are keyed by PR, platform, Python version and dependency inputs.
`runner-bundle.yml` builds and verifies one portable Python runner from the tested
commit. All consumer jobs share it. The runner fixture checks the receipt and
frozen sources against the checkout. Required shared inputs fail if absent or
mismatched; local sessions build once on demand if no shared bundle is supplied.

Windows acceptance shares one prepared player bundle between independent
embedded/native jobs. Callable native tests have a separate step and report;
the general native step excludes them. Reduced-Frida acceptance reuses the runner
build, and release validation checks the Windows candidate.

## Completion and reporting

The Windows gate requires bundle preparation, both acceptance jobs and all three
coverage fragments. The parent gate requires the claim, dependencies, runner and
all test workflows to succeed. A trusted job publishes that result as `manual-ci`.

Jobs retain JUnit and raw coverage plus suite XML/HTML reports. Callable
coverage measures `hook_manager.callables`; the parent report excludes separately
owned modules, using exclusions derived from that same suite table. Integration
reports span several components and can overlap their component reports;
percentages stay separate and are never added or averaged.
Coverage measures Python run by pytest. Wasm execution and embedded child
processes remain uninstrumented. Windows aggregation rejects missing or corrupt fragments.

After success, the reporting workflow parses only that run's XML without running
PR code. It requires every portable suite/platform and Windows acceptance report,
keeps percentages separate and distinguishes the workflow commit from the tested
PR head. Reports never mix different runs or commits.

[Developer performance comparisons](../benchmarks/regulations/README.md) stay
outside CI, reuse matching runner artefacts and separate diagnostics from timings.
