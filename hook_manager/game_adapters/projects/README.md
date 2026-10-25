# Native development-project support

This private package maps aero projects/spec upgrades and current/future chassis
projects. It prepares final terms on private inputs and supplies checked UI/AI
caller preparation to trusted guard users. It imports no regulation dispatch,
launcher definitions or sandbox runtime. Importing installs no hooks and forces
no game modules to load.

Runtime checks method paths and exposed signatures, including defaults and
annotations. Matching signatures accept body changes; there is no bytecode or
executable hash gate.

| Native entry | Native refusal | Native action |
| --- | --- | --- |
| `AeroDevelopmentManager.start_project` | `False` | Checks eligibility, limits staff, chooses a budget, creates a project and can charge its concept fee. |
| `AeroDevelopmentManager.start_spec_upgrade` | `False` | Checks eligibility and available specifications, limits staff, chooses a budget and creates the upgrade. |
| `DevelopmentManager.start_project` | `False` | Handles current and future chassis projects. Checks eligibility, limits staff, chooses a total budget and applies the current-package budget multiplier where relevant. |

## Preparing terms

`ProjectPlanner(state)` uses the game's loaded helpers to prepare a proposed
project. The [regulation adapter](../regulations/project_adapter.py) reports
incompatibility and installs the checks.

`prepare(target, arguments)` takes a private `projects.*` target name and the
native arguments, including defaults. It returns copied `clock`, `effective` and `proposal`
data: employer, aero/chassis family, action, part/period/package/budget and final
staff, technical staff and spend terms. Regulation schema/scope/actor/operation
fields are assembled separately by `regulations/contexts.py`.

Future chassis proposals also include `proposal.technical_regulation`: the native
era `key`, `severity`, `retention`, `concept_strength`,
`concept_attribute_multipliers` and `upcoming` flag. Until a change is announced,
the game supplies the current profile. After announcement it supplies the future
profile. These operational effects do not apply to aero/spec or current chassis
admission, so those proposals omit the field.

`TechnicalProfile` in `technical_profile.py` checks the native read helpers once.
It runs them on a copied technical choice record because native reads normalise
that record. The definition is borrowed read-only, and the returned nested data
is copied. No live career record changes during preparation. Future-project
guards compare severity and announcement inputs around validation as well as
the ordinary rule choices; a change refuses that attempt without disabling mods.

Native eligibility helpers can change data: project readers create missing
records, part readers normalise history, and budget/staff helpers can initialise data.
The snapshot copies only headquarters, allocations and package choice needed by
inspected helpers, borrowing stable budget definitions read-only. Neither starts
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

Project preparation tests have their own child workflow under the game-adapter
tests and reuse prepared CI dependencies. Developer probes compare native methods
on separate test data with the game's Python interpreter. Those probes do not
establish live-game acceptance.
