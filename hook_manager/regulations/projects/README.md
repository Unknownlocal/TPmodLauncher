# Development-project mapping and proposal planning

This private Step 5 package identifies the native methods that create aero projects,
aero spec upgrades and current/future chassis projects. `project_targets()` returns
fresh compatibility descriptors for the inspected methods. It installs nothing,
imports no game modules and accepts no mod-supplied targets. These files are not
in the bridge's fixed source list yet. Public regulation admission remains closed.

Inspected executable SHA256:
`e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773`.
This differs from the executable recorded in the original local coverage map.
The current signatures and runtime code fingerprints were read from the installed
archive, not copied from that older inventory. This hash records where the
catalogue was inspected; it is not a runtime whole-executable gate.

| Native entry | Source line | Native refusal | Normalized inputs / first writes |
| --- | ---: | --- | --- |
| `AeroDevelopmentManager.start_project` | 475 | `False` | Checks eligibility, initializes aero dictionaries, clamps both staff counts, chooses a budget, creates a project and can charge its concept fee. |
| `AeroDevelopmentManager.start_spec_upgrade` | 543 | `False` | Checks eligibility/spec availability, normalizes staff and budget, calculates design duration and inserts the upgrade. |
| `DevelopmentManager.start_project` | 983 | `False` | Checks eligibility, initializes development dictionaries, chooses/clamps staff, discards `weekly_spend`, selects a total budget and applies the current-package multiplier before creating the project. Handles both `current` and `future`. |

The recorded lines help inspection and are excluded from compatibility checks.
Descriptors locate methods by path and full signature, then check the specific
implementation, including nested code and exception handling. Filenames and
source-line metadata are excluded recursively. Unrelated changes elsewhere in
the game therefore do not disable this integration. Helpers and callers must
also be checked when enforcement is added; these three identities do not certify
the whole call graph. A body change can still require reinspection even when it
preserves behavior; these checks do not prove equivalence across compiler versions.

## Findings that constrain the next slice

The native eligibility methods are **not read-only**:

- Aero `can_start_project` calls `_project_store`, which uses `setdefault` on the
  team's aero/project dictionaries. `can_start_spec` also calls `_part_record`,
  which fills defaults and rewrites part history.
- Chassis `can_start_project` initializes development dictionaries and calls
  helpers that read/normalize project state. A generic query-phase wrapper would
  run that native code before a mod could refuse it.

The eventual guard must check final normalized terms before the native start
body makes those changes. Raw arguments are insufficient: staff may be clamped,
aero legacy weekly spending selects a budget tier, and chassis ignores that
argument altogether. Do not copy native rule business logic into the launcher;
only normalize the proposal that the mod will examine. `ProjectPlanner` now does
that preparation on private inputs, with the native comparison described below.

The two managers also use different `_absolute_week` conventions: aero uses
`(season_no - 1) * 52 + week_index`, while chassis adds one. Context dates must
follow the appropriate native path rather than a shared guessed formula.

Current direct callers include the aero/development screens, aero
`_ai_try_launch_project` and `AIDevelopment._ensure_current_project`,
`_ensure_future_project`, `_ensure_aero_projects`. They are inspected dependencies,
not completed rejection tests. Their surrounding mutations and handling of
`False` must be checked before UI/AI coverage can be claimed.

The shared-context transport remains the execution path to connect: a single
proposal is prepared once for all relevant owners. The existing generic callable
engine dispatches per owner, so it must not be connected directly to the new
channel signature or used to duplicate a shared proposal per validator.

## Reproduce the compatibility check

The commands below require optional local developer utilities under `tools/`.
That folder is ignored and is not included in a fresh checkout. With those
utilities available, run from the repository root using the game's Python 3.13:

```powershell
libs/python-3.13.16-embed-amd64/python.exe -B tools/regulations/check_projects.py ../TeamPrincipal.exe
```

The developer checker verifies the whole executable hash before reading marshalled
code. It reconstructs only the three declarations using a small allowlist of
declaration operations, then applies the runtime target fingerprint checks and
compares the full signatures. It does not execute archived functions, class
bodies or module imports, and emits no game source/bytecode. No player dependency,
new runner build or CI job is needed. This developer reader still requires the
inspected executable before unmarshalling; that tooling approval is separate from
runtime compatibility. Generalising the archive reader remains a later task.

## Preparing a proposal without changing career state

Use `ProjectPlanner.create(state, report)` once for a native career state.
It checks the three start methods and the 39 staff, budget, upkeep and ERS helpers
used by planning. Helper checks include their bodies, defaults and static-method
status. These checks run at construction, not for every validator or proposal.
Changed relevant implementations or signatures disable project integration. Mods
cannot supply helpers. No whole-executable identity is passed to this runtime API.

`report` is a trusted lifecycle reporting callback. The launcher's existing
reporter writes the same diagnostic to the CLI and session log. On initialization
failure, `create` reports the affected scope/reason and returns `None`; callers
must skip installing this project integration and continue native gameplay and
other integrations. It never substitutes an unchecked planner. The constructor
itself remains strict for developer checks. This failure policy concerns initial
compatibility, not a validator's deny decision or an active dispatch failure.
The project package is still private and not wired into bridge startup yet;
that integration must use this factory and forward its diagnostics to the reporter.

`prepare(target, arguments, initiator=..., operation=...)` expects the fully bound
native arguments, including defaults and `self`. It creates one plain context for
the shared validation transport. No mod handler runs here. Replacing either project
manager invalidates this planner; native integration must construct a new one.

`snapshot.py` copies the selected team's facilities, upkeep, staff assignments
and package choice. Checked native helpers run on temporary manager objects and
can initialize those private dictionaries safely. Their budget/package configuration
is borrowed read-only. The temporary team lookup preserves player-team identity:
the game's upkeep adjustment depends on that identity. Copying the whole career
would add unnecessary work and expose unrelated data to planning.

The context contains employer, project family/action, native `project_key`,
`design_period`, normalized `package_key` (null for aero), budget and clamped staff.
The package is separate from `project_key`: chassis projects are stored under
`current`/`future`, while their package selects the proposed design. Weekly spending
is null because the native starts do not retain it as a promised weekly amount.
Creation weeks use each manager's convention; future chassis rules evaluate next
season. No scheduled end exists at admission, so the end week is null. The returned
data is independently owned and contains no native objects.

The planner predicts normalized terms, not eligibility. It does not run native
eligibility/start/design methods, charge fees, install hooks or acknowledge readiness.
Caller cancellation and transaction handling still require the next integration slice.

## Compare plans with native output

```powershell
libs/python-3.13.16-embed-amd64/python.exe -B tools/regulations/check_project_plans.py ../TeamPrincipal.exe
```

This developer-only check reconstructs selected native helper/start methods on
owned fixture state. It compares planned staff, budgets, packages and creation
weeks with the projects produced by the actual start bodies, and checks that
planning leaves the original team unchanged. Twelve comparisons cover aero starts,
spec upgrades and both chassis periods with player upkeep and AI/ERS variations.
Eligibility and design feedback outside the normalized proposal use fixture stubs;
this is not live-game acceptance. No CI matrix or player archive dependency is added.

Remaining Step 5 work: shared native
dispatch, wrapper/caller cancellation, worker handling, and the personnel/academy,
restoration and scheduled-activation paths. Governance/persistence/presentation
and live acceptance retain their later production gates. This inventory does not
enable gameplay enforcement or acknowledge native readiness.
