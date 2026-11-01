# Staff contracts and team staffing

The staff adapter prepares Formula team and engine-supplier contracts for
`personnel.contract` validators. Use source API `0.4.0` with `regulations`.
Mods check copied proposals; the game's remaining restrictions still apply.

The [personnel API](../personnel/README.md) defines shared identities, contract
fields, offer previews and optional payroll input. The
[regulation API](../regulations/README.md) defines declarations and handler returns.

## Data available to mods

`context["proposal"]` identifies the staff member and employer, with
`contract_kind: "employment"` and `action: "sign"` or `"renew"`.
The employer's `kind` distinguishes `"team"` from `"supplier"`.

Proposed terms include the normalised role, start/end weeks, duration, annual
salary and available bonuses. Money is in millions. Staff status and annual
academy fee are `None`. Contracts can start this season or next season;
temporary cover for a vacant role ends at the next season boundary.

When payroll is requested, team totals include the proposed agreement and
earlier hires in the same staffing operation. Supplier payroll is `None`.
See the [payroll example](../../../examples/staff_spend_mod).

## Whole-team staffing

The same contract validators check each proposed hire when the game staffs a
joining team or fills missing roles, including generated staff. Later proposals
see earlier planned hires.

The launcher applies a completed staffing plan only when required roles are
filled, the relevant career inputs are unchanged and the same rules still
apply. A refused batch discards its draft contracts and generated people.

## Native prestige requirement

A mod's `native_rules` can set `staff_prestige_check` to `false` to bypass the
game's staff prestige minimum. Omitted or `true` keeps it. A boolean in an owned
category can control it for the proposed contract's season.

This affects only that native check. The game's whole-team recovery path already
bypasses ordinary prestige requirements. See the
[native switch format](../regulations/README.md#native-rule-switches).

## Internal responsibilities

`StaffContractPlanner` calculates individual terms. `StaffingPlanner` runs
native selection and signing on temporary records, held by `StaffingPlan`.
`StaffingPublication` checks and applies the result to the original records;
the regulation coordinator owns approval and prevents overlapping changes.
`StaffPrestigeSwitch` owns the optional prestige bypass.

[Personnel support](../personnel/README.md) handles shared negotiations and
feedback. Saved agreements load and take effect normally. These helpers are
private launcher code; importing them installs no hooks.
