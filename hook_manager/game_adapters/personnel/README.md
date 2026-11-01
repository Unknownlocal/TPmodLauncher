# Personnel approaches and contracts

This adapter supports Formula driver and staff negotiations. Mods can check
approaches before talks begin and contract proposals before offers or signings
change the career. Use source API `0.4.0` with `regulations`.

The [regulation API](../regulations/README.md) defines handler declarations,
common context fields and allow/deny responses. The fields below are under
`context["proposal"]`. They are copied data, not live game objects.

## Approaches: `personnel.approach`

This scope covers driver and staff approaches to Formula teams.

| Field | Meaning |
| --- | --- |
| `action` | `"approach"`. |
| `person` | Candidate identity, as described below. |
| `employer` | Team identity, as described below. |
| `role` | Intended employment role. |

`context["effective"]` gives the intended season and start week; `end_week`
is `None`. Salary and contract duration are not available at this stage.
An allowed approach does not approve a later contract.

## Contracts: `personnel.contract`

This scope covers [driver](../drivers/README.md), [staff](../staff/README.md)
and [academy](../academy/README.md) signings and renewals.

| Field | Meaning |
| --- | --- |
| `action` | `"sign"` or `"renew"`. |
| `person` | Candidate identity. |
| `employer` | Team, supplier or academy identity. |
| `contract_kind` | `"employment"` or `"academy"`. |
| `existing` | Previous agreement's terms, or `None`. |
| `proposed` | Resulting agreement's terms. |

Driver and staff offer submissions use `context["operation"] == "preview"`.
Final signings use `"commit"` and are checked again. Already accepted agreements
load and take effect normally without another mod check.

Both `existing` and `proposed` use these term fields:

| Field | Meaning |
| --- | --- |
| `role`, `status` | Employment role and driver status where available. |
| `start_week`, `end_week` | Absolute game weeks; the end is exclusive. |
| `duration_weeks`, `duration_seasons` | Full agreement length; employment uses weeks and leaves seasons as `None`. |
| `annual_salary_m` | Annual employment salary, in millions. |
| `annual_retainer_m` | Annual academy fee, in millions. |
| `bonuses_m` | `point`, `win` and `podium` amounts, in millions. |

Unavailable values are `None`. Use the relevant adapter's explanation for
differences between employment and academy terms.

## Person and employer identities

A person has `kind` (`"driver"` or `"staff"`), `ref`, `name`,
`nationality`, `age` and `role`. The identity's role is the person's stored
role; use proposed contract terms for the employment role.

An employer has `kind` (`"team"`, `"supplier"` or `"academy"`), `ref`,
`name`, `nationality`, `prestige_base`, `heritage_prestige` and
`form_prestige`. Missing values are `None`. References identify loaded objects;
do not store them as permanent save-file IDs.

## Optional payroll input

A `personnel.contract` validator can declare:

```json
"inputs": ["employer_payroll"]
```

This adds `proposal["employer"]["payroll"]`:

| Field | Meaning |
| --- | --- |
| `as_of_week` | Today for an immediate/backdated deal, or the future start week. |
| `staff_annual_salary_m` | Team's annual staff base salaries, in millions. |
| `driver_annual_salary_m` | Team's annual driver base salaries, in millions. |

Totals include the proposed agreement in place of the candidate's old deal,
scheduled agreements and earlier hires in the same staffing operation.
They exclude bonuses, buyouts and academy fees. Academy and supplier proposals
give `None` for payroll. No payroll request means no roster scan.

The [payroll example](../../../examples/staff_spend_mod) shows how to apply a cap.

## Internal responsibilities

`PersonnelApproach` prepares approach data; `PersonnelOffer` translates offers
into signing terms. Negotiation wrappers keep draft offers and mail separate
from live records. Payroll support calculates totals and rechecks them before
allowing changes. Driver and staff planners supply their own contract terms.

Feedback uses the game's submission errors and shared refusal dialogs. Failed
required checks stop the action; completed signings are not undone. Supported
weekly and season-end calls grant game workers access only while those calls run.
These helpers are private launcher code and expose no direct game-call API.
