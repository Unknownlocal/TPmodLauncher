# Development-project native mapping: entry-point inventory

This first Step 5 slice identifies the native methods that create aero projects,
aero spec upgrades and current/future chassis projects. `project_targets()` returns
fresh compatibility descriptors for the inspected executable. It installs nothing,
imports no game modules and accepts no mod-supplied targets. These files are not
in the bridge's fixed source list yet. Public regulation admission remains closed.

Inspected executable SHA256:
`e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773`.
This differs from the executable recorded in the original local coverage map.
The current signatures and runtime code fingerprints were read from the installed
archive, not copied from that older inventory. Unknown builds are rejected.

| Native entry | Source line | Native refusal | Normalized inputs / first writes |
| --- | ---: | --- | --- |
| `AeroDevelopmentManager.start_project` | 475 | `False` | Checks eligibility, initializes aero dictionaries, clamps both staff counts, chooses a budget, creates a project and can charge its concept fee. |
| `AeroDevelopmentManager.start_spec_upgrade` | 543 | `False` | Checks eligibility/spec availability, normalizes staff and budget, calculates design duration and inserts the upgrade. |
| `DevelopmentManager.start_project` | 983 | `False` | Checks eligibility, initializes development dictionaries, chooses/clamps staff, discards `weekly_spend`, selects a total budget and applies the current-package multiplier before creating the project. Handles both `current` and `future`. |

The recorded lines help inspection; the descriptors verify code fingerprints and
complete signatures, including defaults and annotations. Lines alone are not
compatibility checks. Helpers and callers must also be checked when enforcement
is added; these three identities do not certify the whole call graph.

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
only normalize the proposal that the mod will examine. The read-only planning
approach still needs implementation and conformance checks against native output.

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

From the repository root, with the game's Python 3.13 interpreter:

```powershell
libs/python-3.13.16-embed-amd64/python.exe -B tools/regulations/check_projects.py ../TeamPrincipal.exe
```

The developer checker verifies the whole executable hash before reading marshalled
code. It reconstructs only the three declarations using a small allowlist of
declaration operations, then applies the runtime target fingerprint checks and
compares the full signatures. It does not execute archived functions, class
bodies or module imports, and emits no game source/bytecode. No player dependency,
new runner build or CI job is needed. A changed executable requires fresh mapping
inspection, not replacing the hash merely to make this command pass.

Remaining Step 5 work: read-only normalized context preparation, shared native
dispatch, wrapper/caller cancellation, worker handling, and the personnel/academy,
restoration and scheduled-activation paths. Governance/persistence/presentation
and live acceptance retain their later production gates. This inventory does not
enable gameplay enforcement or acknowledge native readiness.
