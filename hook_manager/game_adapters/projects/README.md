# Development projects

The project adapter prepares Formula aero and chassis proposals for
`development.project` validators. Use source API `0.4.0` with `regulations`.
Mods receive copied final terms before the project starts. An allowance still
leaves the game's eligibility and capacity checks in place.

The [regulation API](../regulations/README.md) defines declarations, common
context fields and allow/deny responses. These checks use `operation: "commit"`.

## Supported actions

| Project | `family` | `action` | `design_period` |
| --- | --- | --- | --- |
| Aero development | `"aero"` | `"start"` | `"current"` |
| Aero specification upgrade | `"aero"` | `"spec_upgrade"` | `"current"` |
| Current chassis | `"chassis"` | `"start"` | `"current"` |
| Future chassis | `"chassis"` | `"start"` | `"future"` |

## Data available to mods

Fields under `context["proposal"]`:

| Field | Meaning |
| --- | --- |
| `employer` | Team `ref`, `name` and `kind: "team"`. The reference is not a permanent save-file ID. |
| `family`, `action`, `design_period` | Values from the table above. |
| `project_key` | Aero part key, or `"current"`/`"future"` for chassis. |
| `package_key` | Chassis package key; `None` for aero. |
| `budget_key` | Budget tier chosen by the native calculation. |
| `total_budget_m` | Calculated total budget, in millions. |
| `staff`, `technical_staff` | Final allocations after the game's limits. |
| `weekly_spend_m` | `None`; project admission does not promise a weekly spend. |

`context["effective"]["season"]` is next season for future chassis and the
current season otherwise. `start_week` is the creation week; `end_week` is
`None` because there is no scheduled end at admission. Aero and chassis retain
their own native absolute-week calculations.

Current chassis packages can multiply the budget; future chassis does not use
that multiplier. An older aero weekly-spend argument selects a budget tier
rather than becoming a separate promised cost.

## Future technical regulations

Only future chassis proposals include `proposal["technical_regulation"]`:

- `key`: native technical era.
- `severity`, `retention` and `concept_strength`: native development effects.
- `concept_attribute_multipliers`: effects on individual concept attributes.
- `upcoming`: whether the profile represents an announced future change.

Before a change is announced, the game supplies its current profile. After the
announcement it supplies the future profile. This calculated input is separate
from the rule option passed as the handler's second argument.

## Internal responsibilities

`ProjectPlanner` uses native budget and staff helpers on small working copies.
It does not start a project, charge money or run eligibility methods.
`TechnicalProfile` reads technical choices without changing live records.
`ProjectCallers` keeps AI preparation private until an allowed start.

The regulation adapter selects rules and checks relevant inputs again before
accepting a reply. A changed input refuses that attempt. These helpers are
private launcher code; importing them installs no hooks or mod registrations.
