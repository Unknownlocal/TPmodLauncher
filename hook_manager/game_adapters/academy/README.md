# Academy memberships

The academy adapter prepares Formula membership signings and renewals for
`personnel.contract` validators. Use source API `0.4.0` with `regulations`.
Mods receive copied proposals; the game retains its eligibility checks, fees
and record updates after mod approval.

The [personnel API](../personnel/README.md) defines shared identities and contract
fields. The [regulation API](../regulations/README.md) defines declarations,
common context fields and handler returns.

## Data available to mods

Under `context["proposal"]`:

- `contract_kind` is `"academy"`; `action` is `"sign"` or `"renew"`.
- `person` identifies the driver and `employer` identifies the academy.
  The employer's `kind` is `"academy"`.
- `existing` describes the previous agreement, or is `None`.
- `proposed` describes the full resulting membership, including
  `duration_seasons`, `duration_weeks`, `start_week` and exclusive `end_week`.
- `proposed["annual_retainer_m"]` is the annual membership fee in millions.
  Salary, role and status are `None`; each value in `bonuses_m` is `None`.
- Requested employer payroll is `None`: membership fees are separate from
  employment salaries.

A new signing's term is limited by the game's age cap. A renewal keeps its
original start and annual fee; its proposed duration is the full extended term,
not just the extension. The effective rule season is the current season unless
the membership starts in the future. Checks use `operation: "commit"`.

The [nationality example](../../../examples/team_nationality_mod) includes
academy agreements.

## Internal responsibilities

`AcademyContractPlanner` calculates membership data without signing or charging
fees. Term helpers use the game's season calendar. Player and AI wrappers keep
preparation separate from live records, and retain refusal messages for the
academy screen. The regulation adapter selects rules and controls signing.

These are private launcher modules. Importing them installs nothing, and mods
cannot call their game functions directly.
