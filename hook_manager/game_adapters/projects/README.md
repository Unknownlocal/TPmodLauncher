# Native development-project support

This private package maps aero projects/spec upgrades and current/future chassis
projects. It prepares final terms on private inputs and supplies checked UI/AI
caller preparation to trusted guard users. It imports no regulation dispatch,
launcher definitions or sandbox runtime. Importing installs no hooks and forces
no game modules to load.

Inspected executable provenance:
`e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773`.
Runtime checks method paths/signatures and relevant implementations/defaults;
source locations and unrelated executable contents are excluded. Compiler or
relevant body changes can still require reinspection. The checks do not certify
the complete game call graph.

| Native entry | Source line | Native refusal | Normalized inputs / first writes |
| --- | ---: | --- | --- |
| `AeroDevelopmentManager.start_project` | 475 | `False` | Checks eligibility, initializes aero dictionaries, clamps both staff counts, chooses a budget, creates a project and can charge its concept fee. |
| `AeroDevelopmentManager.start_spec_upgrade` | 543 | `False` | Checks eligibility/spec availability, normalizes staff and budget, calculates design duration and inserts the upgrade. |
| `DevelopmentManager.start_project` | 983 | `False` | Checks eligibility, initializes development dictionaries, chooses/clamps staff, discards `weekly_spend`, selects a total budget and applies the current-package multiplier before creating the project. Handles both `current` and `future`. |

## Preparing terms

Construct `ProjectPlanner(state)` after ordinary native imports finish. It raises
on incompatibility. The consuming adapter owns reporting and installation; the
regulation consumer is `../regulations/project_adapter.py`.

`prepare(target, arguments)` takes a private `projects.*` target name and fully
bound native arguments. It returns owned `clock`, `effective` and `proposal`
data: employer, aero/chassis family, action, part/period/package/budget and final
staff, technical staff and spend terms. Regulation schema/scope/actor/operation
fields are assembled separately by `regulations/contexts.py`.

Native eligibility helpers are not read-only: project stores use `setdefault`,
part readers normalize history, and budget/staff helpers can initialize data.
The snapshot copies only headquarters, allocations and package choice needed by
checked helpers, borrowing stable budget definitions read-only. Neither starts
nor eligibility methods run during planning; there is no whole-career copy.

Final staff is clamped with native helpers. Aero legacy weekly spend selects a
budget tier; chassis ignores it. Current chassis package can multiply its budget;
future chassis does not. No promised weekly spend or scheduled end exists in the
native stored project. Aero/chassis use different absolute-week offsets, retained
individually; future chassis evaluates the next season despite creation today.

## Preparing callers

`ProjectCallers` uses the trusted guard lifecycle to keep AI preparation private.
The AI selects a current chassis package before start, but start itself writes it
on success, so the earlier write is suppressed. Project stores and part-history
reads receive private containers during preparation; allowed starts use real
stores. The legacy aero AI budget pool uses a draft, publishing its inspected
output fields only after successful start. Caller context retains player/AI
identity, and registry changes/shutdown wait for the operation to finish.

[Regulation integration](../regulations/README.md) supplies policy selection,
shared validator dispatch and native guard bindings. It checks all three starts
and eleven callers/helpers before installation and restores partial failure.
Native support itself has no mod registration or rule-selection responsibility.

Optional ignored tools under `tools/regulations/` compare actual native bodies on
owned fixtures with game Python 3.13: three entry descriptors, twelve planning
comparisons and twenty-seven guard/caller checks. Eligibility/finance/presentation
dependencies outside mapped calculations use fixture stubs. This is not live
career, complete worker-transaction or public-admission acceptance. Developer
archive generalization remains deferred. Portable checks remain in the existing
hook-manager suite; no separate CI jobs or runner builds are introduced.
