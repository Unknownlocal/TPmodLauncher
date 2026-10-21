# Native staff employment support

`StaffContractPlanner(state).prepare(bound_arguments)` prepares team or engine
supplier employment terms using the checked native signing/calendar helpers.
It returns `clock`, `effective` and `proposal` data. It does not sign a contract,
change eligibility or install regulation guards. The regulation adapter adds its
own schema, scope and operation metadata when it consumes this model.

The helper catalogue checks the relevant functions once when the planner is
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

## Inspected native entry points

The line numbers below are inspection references, not runtime selectors. Relevant
function checks exclude source locations; the archive hash in `targets.py` records
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
