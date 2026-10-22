# Native staff employment support

`StaffContractPlanner(state).prepare(bound_arguments)` prepares team or engine
supplier employment terms using the checked native signing/calendar helpers.
It returns `clock`, `effective` and `proposal` data. It does not sign a contract,
change eligibility or install regulation guards. The regulation adapter adds its
own schema, scope and operation metadata when it consumes this model.

The helper catalogue checks the relevant call signatures once when the planner is
constructed. Preparation binds the retained read-only helpers to a small query
owner and borrows the live roster, teams and suppliers. These helpers do not
normalize records or populate caches, so copying the career is unnecessary.
Returned data contains only copied proposal fields and opaque process-local refs.
An employer's `name` is its native owner key: a team name or supplier ID.

Automatic employer selection prefers an eligible team over a supplier with the
same key, matching native signing. Explicit starts accept this or next season;
an occupied role otherwise starts next season. Current cover can shorten even a
multi-year request to the remainder of this season. Staff bonus conversion keeps
negative amounts; an absent bonus object leaves the amounts null. These differ
from driver signing and remain in the staff planner. Shared record formatting
lives in `../employment.py`; regulation policy remains mod-owned Python code.

## Required team staffing preparation

`StaffingPlanner(state)` in `staffing.py` prepares native joining-team onboarding
or missing-staff recovery. Construct it after native imports complete and before
signing hooks acquire their methods, like the existing contract planner. Each
`prepare_onboarding(team, contract_years=1)` or `prepare_recovery(roles=None)` call
uses a fresh private manager/roster and retains checked native generation, ranking,
name-pool and signing behavior. It installs no wrappers or regulation dispatcher.

Existing people, teams, name lists and historical rows are read by the inspected
queries. Roster/history roots are copied; a selected person is copied once before
private signing. Subsequent choices see that planned contract. Generated people
are retained separately in their unsigned state, including unselected population
seeds. Neither native effects nor generation are published to the live career.
Preparation supports the native team recovery path that bypasses ordinary prestige
requirements; a newly introduced supplier or prestige-checking signing path is
rejected before it can run against borrowed owner data.

The result is a private `StaffingPlan` with the native list/`None` result, ordered
`StaffSigning` attempts, generated people and newly added history keys. Each attempt
keeps canonical manager/person arguments, owned normalized `prepared` proposal data
and a `native_accepted` flag (None if validation withheld native execution).
Existing person/employer refs match live objects;
generated refs match the retained unsigned objects. The flag records native
planning success, not permission from a regulation validator. These models have
one class per file and are never passed to mods.

An optional trusted `validate(prepared)` callback checks each contract before
private native signing; a withheld contract returns False to the native caller.
The plan records the native staffing queries still unsatisfied at the end.
`StaffingPublication` retains only selected-person signing changes, generated
people and new history keys. It checks the clock, roster/person data, team
eligibility, name-pool data and history roots/keys for staleness. Existing history
rows are borrowed and preserved. Publication keeps canonical people, roster/history
containers and existing mutable values that native code edited in place.

The private regulations `StaffingCoordinator` consumes this support and the
installed personnel guards. Construct the staffing planner before hooks retain
signing, then pass it and the guards to the coordinator. `onboard(...)` and
`recover(...)` validate on the private native run and publish only complete,
current plans under the acknowledged policy. They return a `StaffingResult`; its
`published` field determines success, independently of native list/None results.
No native signing is replayed. Native errors propagate with private work discarded;
native False, policy deny and unavailable validation remain distinct.

The coordinator accepts the main thread or a native worker previously enrolled
with `guards.enroll(thread)` and running inside `guards.operation()`. Enrollment
alone does not grant execution. A shared nonblocking reservation excludes other
coordinated staffing and guarded signing from the initial snapshot through final
publication. Busy attempts return unavailable without disabling healthy validators;
a later fresh run can retry. Workers also require active staff signing guards;
after their last owner is removed, worker calls return unavailable before planning,
because ordinary native signing has been restored and cannot share the reservation.
Main-thread calls without validators retain their existing behavior. It installs
no outer hooks. Enclosing load/joining/rollover mutations, actual worker
creation/admission, lifecycle wiring and complete personnel readiness retain their
later Step 5 gates.

## Inspected native entry points

The line numbers below are inspection references, not runtime selectors. Runtime
compatibility checks signatures only; the archive hash in `targets.py` records
inspection provenance only. No compiled engine or executable is modified.

| Native point | Behavior / remaining enforcement work |
| --- | --- |
| `StaffManager.sign_staff` (1209) | Team/supplier signing and renewal. The personnel regulation adapter now guards the entire body, before prestige queries, contract assignment and morale effects. |
| `PersonnelNegotiationManager._execute_offer` (4136, staff branch 4225) | One shared personnel adapter owns driver/staff negotiation callers. Prestige initialization and the optional entry hint remain private until allowance; refusal cancels player acceptance before backup/closing writes. |
| `StaffManager.onboard_joining_team_staff` (1614) | Uses signing and checks its result, but can create candidates beforehand. Enclosing transaction coverage remains required. |
| `StaffManager.ensure_active_teams_have_staff` (1701) | Uses signing without checking its result. A signing refusal alone cannot cancel recovery bookkeeping. |
| `StaffManager.transfer_head_of_engine_contract` (752) | Reassigns current/future contract owners directly. Called by `GameState._apply_engine_takeover` and `EngineManager._merge_external_supplier_into_works_team`; needs enclosing transaction coverage. |
| `StaffManager.load` (300), `rollover_season` (1433) | Direct contract writes; rollover changes age/skills before activation. Require load/rollover preflight before mutations. |
| `GameState._activate_future_staff_contracts` (11953), `finalize_and_rollover_season` (13396) | Activation and fallback writes bypass signing; remain release coverage gates. |
| `StaffManager.enforce_head_of_engine_eligibility` (727) | Can remove agreements during load/season/engine-plant configuration. Must be included in enclosing transition checks. |

`PersonnelAdapter.create` in `../regulations/personnel_adapter.py` consumes this
planner and installs signing/caller guards with compatibility diagnostics and
installation rollback. Enclosing transactions, academy employment, startup wiring
and public regulation admission remain pending.
The generic archive-inspection refactor stays deferred until the functional
regulation framework is complete, before broad compatibility acceptance.
