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
native imports raise; the future consuming adapter owns CLI/log reporting and
installation. Importing this package installs no hooks and forces no game imports.

## Inspected game paths

These source line numbers describe the inspected build; runtime lookup uses named
paths and signatures. Native archive tooling and evidence stay local/ignored.

| Native path | Observed behavior / remaining enforcement work |
| --- | --- |
| `AcademyManager.sign_driver` (257) | Calls mutating `ensure_team_state` (24) before slot/eligibility checks. It writes membership list, team contract and driver link, then charges the signing fee. A future guard must precede the whole body. |
| `AcademyManager.renew_contract` (691) | Normalizes academy state before checking membership; increases the stored full term and rebinds the driver's link. Separate entry point from signing. Full native renewal eligibility remains native code. |
| `AcademyScreen.handle_event` (808) | Calls renewal at879 and signing at1069. Success controls modal/selection changes; the native caller discards the returned reason and uses generic action feedback. Refusal feedback needs caller integration. |
| `AcademyManager.ai_sign_drivers` (922) | Normalizes academy state and can write `ai_last_sign_season` before signing; removes the candidate from its pool regardless of success. Checks the signing boolean before reducing available slots. Requires enclosing preparation/cancellation handling. |
| `AcademyManager.ai_renew_contracts` (1138) | Normalizes academy state and ignores the renewal result. Call-site protection must account for that preparation; a false return alone is not whole-transaction cancellation. |
| `AcademyManager.progress_season` (778), `sync_driver_links` (819) | Normalize/expire membership, bill retainers and restore links directly. Initial/load/rollover admission needs enclosing preflight. |
| `AcademyManager.arrange_seat` (587), `apply_main_series_placements` (431) | Write placements and employment contracts directly, bypassing ordinary signing. Must join later employment/activation transaction coverage. |
| `AcademyManager.release_driver` (716), `_clear_driver` (747), `release_team` (765) | Remove membership/placement links; release can charge a remaining-term penalty. These are separate lifecycle transitions. |

Only proposal preparation is implemented here. Slot/history/employment eligibility
and native renewal age refusal are not replaced by launcher policy. Signing/renewal
guards, enclosing transactions, startup wiring and public admission remain pending.
The generic archive inspection refactor stays deferred until the functional
regulation framework is complete, before broad compatibility acceptance.

Two portable collected tests cover ownership and strict signature failure.
Ignored `tools/regulations/check_academy_plans.py` compares actual native signing
and renewal results on owned fixtures. Eligibility/driver-centre configuration is
supplied by the fixture; no live save, module constructor or compiled kernel is
modified. This evidence does not claim complete career or caller enforcement.
