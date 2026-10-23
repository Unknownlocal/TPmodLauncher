# Native academy membership support

`AcademyContractPlanner(state).prepare(target, bound_arguments)` prepares Formula
academy signing (`academy.sign`) or renewal (`academy.renew`) terms without
running either native method. Arguments include `self` and applied defaults.
The result contains `clock`, `effective` and `proposal`, which the regulation
consumer can wrap in the existing `personnel.contract` context.

The proposal identifies a driver, employer kind `academy` and contract kind
`academy`. Membership is separate from employment salary. Role/status/bonuses
and salary are null; `annual_retainer_m` describes native `annual_cost_m`, the
annual billing field. A missing annual billing field remains null, even if an
older `retainer_m` field exists. Native new signing sets both fields to the same
talent-based fee, charges that fee and records membership in the current season.
Planning only describes that agreement; it charges nothing.

Signing normalizes the requested term against the native age cap. Renewal adds
the native clamped extension to the original full term; it preserves the original
start season and annual cost. Renewal reads the destination team's canonical
contract rather than the driver's potentially stale cached membership link.
Existing saved term/start defaults match the inspected native queries.

Native membership records store seasons. `duration_seasons` preserves that full
term. `terms.py` obtains start/end weeks by calling the retained native season
calendar on tiny private clocks for each boundary. It does not assume 52 weeks
per season. Renewal is evaluated in the current season, or the stored start season
if it is still in the future; the reported full term can begin in an earlier year.

The constructor checks native paths/signatures once, including its read-only
helpers. Matching signatures accept changed implementations. Missing or incomplete
native imports raise; the consuming regulation adapter owns CLI/log reporting and
installation. Importing this package installs no hooks and forces no game imports.

## Inspected game paths

These source line numbers describe the inspected build; runtime lookup uses named
paths and signatures. Native archive tooling and evidence stay local/ignored.

| Native path | Observed behavior / remaining enforcement work |
| --- | --- |
| `AcademyManager.sign_driver` (257) | Calls mutating `ensure_team_state` (24) before slot/eligibility checks. The regulation consumer now guards the whole body, before membership/link/financial writes. |
| `AcademyManager.renew_contract` (691) | Separate guarded entry point; native renewal eligibility, full-term extension and link updates run only after allowance. |
| `AcademyScreen.handle_event` (808) | Calls renewal at879 and signing at1069. Native success checks preserve modal/selection state on refusal. The consumer retains the refusal reason in an adapter-owned temporary notice drawn after the native screen. |
| `AcademyManager.ai_sign_drivers` (922) | Academy normalization, `ai_last_sign_season`, query inputs and random draws use private preparation state. Each signing attempt crosses the live guard; refused local pool removal cannot change career data. |
| `AcademyManager.ai_renew_contracts` (1138) | Its ignored result is safe because all preparation is private; only the guarded live renewal can change membership. |
| `AcademyManager.progress_season` (778), `sync_driver_links` (819) | Normalize/expire membership, bill retainers and restore links directly. Initial/load/rollover admission needs enclosing preflight. |
| `AcademyManager.arrange_seat` (587), `apply_main_series_placements` (431) | Write placements and employment contracts directly, bypassing ordinary signing. Newly created employment needs later signing integration; carrying forward accepted agreements remains native. |
| `AcademyManager.release_driver` (716), `_clear_driver` (747), `release_team` (765) | Remove membership/placement links; release can charge a remaining-term penalty. These are separate lifecycle transitions. |

`../regulations/academy_adapter.py` composes this support with shared regulation
dispatch, pair-shaped native results, compatibility diagnostics and atomic install
rollback. Native caller methods run on a shallow career view with only academy
records copied. Driver active-contract reads normalize private agreements with no
live cache writes; no driver query wrapper competes with the personnel adapter.
Before an allowed native action, only the selected team's preparation and consumed
random state are published. Actual native signing runs once on real objects, then
the view refreshes its roster/contracts/balance. Canonical contract dictionaries
and cached driver-link aliases are retained. A refusal discards all still-pending
batch preparation; earlier allowed actions remain committed. A pass without any
mod refusal preserves ordinary native preparation, even when native checks refuse.

Slot/history/employment eligibility and native renewal age refusal remain game
code. Unknown caller threads are rejected; worker use requires the adapter's
explicit enrollment/operation scope. Broader placement/load/rollover transactions,
startup wiring and public admission remain pending.
The generic archive inspection refactor stays deferred until the functional
regulation framework is complete, before broad compatibility acceptance.

Two portable planner cases and two guard cases cover ownership, compatibility,
AI preparation and rollback.
Ignored `tools/regulations/check_academy_plans.py` compares actual native signing
and renewal results on owned fixtures. Eligibility/driver-centre configuration is
supplied by the fixture; no live save, module constructor or compiled kernel is
modified. `check_academy_guards.py` also exercises actual native immediate caller
paths on owned fixtures, with minimal presentation primitives. This evidence does
not claim complete career transactions or live screen/layout acceptance.
