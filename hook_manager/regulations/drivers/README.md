# Driver employment mapping and proposal planning

This private package prepares the `personnel.contract` context for Formula driver
employment. It installs no hooks, forces no game imports and does not enable a
mod capability or acknowledge native readiness. Driver guards and caller
cancellation follow in a separate chunk. Staff and academy contracts are separate
native paths; their implementations remain pending.

The inspected executable was
`e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773`.
That hash records developer inspection provenance only. Runtime compatibility
checks the signing signature and relevant implementations, excluding filenames
and source-line metadata. The original local coverage map describes an older
image; the following locations were re-read from the currently installed archive.

| Native path | Inspected behavior / later guard requirement |
| --- | --- |
| `DriverManager.sign_driver`, line 3954 | Chooses dates from current/future capacity, supports shortened reserve cover, normalizes bonuses/status, stores active or future employment and reconciles hierarchy. Loan-return branches can clear contracts at 4028/4043 before later validation; final assignment is at 4121/4126. Guard before the whole body. |
| `PersonnelNegotiationManager.accept_player_driver_proposal`, line 1702 | Converts proposal fields and requests next-season signing. It handles `False` before accepted/viewed/save writes at 1766 onward. Entry-specific branches belong to Oval, outside this mapper. |
| `PersonnelNegotiationManager._execute_offer`, line 4136 | Normalizes rookie offer duration before signing. It returns the signing boolean and records a final-validation reason on refusal. Enclosing offer resolution still needs transaction/caller checks. |
| `submit_offer`, line 2182; `ai_manage_drivers`, line 3435 | Offer/market flows converge through negotiation execution. A submitted offer is not a final contract, and incomplete approaches cannot authorize signing. |
| `GameState._activate_future_driver_contracts`, line 11939 | Activates contracts directly during week advancement, bypassing signing. It needs enclosing transaction preflight rather than a signing-only guard. |
| `GameState.finalize_and_rollover_season`, line 13396 | Contains fallback signing and direct active/future assignments. Return values, roster recovery and decorator/worker mutations remain later acceptance work. |
| Initial/load/exit paths | `DriverManager.load_initial` 668 and `load` 832 restore records directly. Retirement, fatalities, firing and expiration remove agreements; status scheduling also modifies records outside signing. Restore/activation and reassignment coverage remain release gates. |

## Preparing one proposal

Use `DriverContractPlanner.create(state, report)` after the ordinary driver and
Formula-entry modules have completed loading. It returns a checked planner or
reports a bounded compatibility failure through the trusted CLI/file lifecycle
reporter and returns `None`. The constructor remains strict for developer checks.
No game module is forced to import. Relevant code changes still require inspection;
a matching signature alone does not establish compatible calculations.

`prepare(arguments, initiator=..., operation=...)` expects the fully bound
`sign_driver` arguments including defaults and `self`. It supports `preview` and
`commit`, not activation/restoration. It predicts normalized terms, not native
eligibility. A prepared preview never authorizes a later commit. Existing native
age/eligibility/relationship/seat checks remain game-owned; mod conditions remain
entirely in mod handlers.

The planner checks the sign body and 30 helper implementations/defaults once.
Shared `method_group` checking lives in the private callable package and is reused
by project planning. `snapshot.py` copies the candidate's contract records and the
destination team's relevant current/future roster. Native queries may normalize
only those copies. The source query cache is not reused because copies have
different identities and signing invalidates that cache anyway. A newly generated
candidate is not added to the scratch roster unless present in the native roster.

Calendar and entry-capacity helpers read native team plans and adopted regulation
definitions/governance without editing them; these stable inputs are borrowed
read-only, not repeatedly serialized or copied into handler arguments. No full
career copy or mod-specific nationality/duration logic is added.

Normalization follows the inspected signing path:

- Automatic current/future dates depend on the candidate's active agreement and
  available current seats. The current driver week includes the native season
  start offset; it is not the aero/chassis convention.
- Player reserve cover can run from today to next season rather than a full
  requested year. Its actual weeks determine the resulting term and exclusive end.
- Returned academy loans are removed only from private records before date
  queries, while the prior agreement is retained in the context.
- The nested native bonus converter runs with a fresh owned input cell. It
  preserves the game's treatment of absent, invalid and negative bonus values
  without duplicating that conversion in launcher code.
- Native post-signing hierarchy helpers can change the resulting driver status.
  They run on private contracts; their status scheduling suppresses email/morale
  effects, which occur after the checked status writes.

The context carries person, employer, prior agreement and resulting terms. The
current agreement takes precedence for `existing`, falling back to a scheduled
agreement only if no current record exists. Same-employer continuation is labeled
`renew`; other signing is `sign`. The proposed role/status preserve native output.
`person.role` is null if the driver object has no separate role attribute; the
employment role remains in `proposed.role`.

Employment records store weeks, not a separate season-count/retainer field, so
`duration_seasons` and `annual_retainer_m` are null. Unavailable old-record fields
remain null. Formula planning does not accept entry-specific signing or Oval
contracts. Invalid/unrepresentable data fails preparation rather than inventing
an approval or returning Python objects. Returned contexts are independently owned.

## Developer evidence

The optional local `tools/regulations/check_driver_plans.py` reconstructs selected
actual native signing/helper bodies on owned fixtures with the game's Python 3.13.
`tools/` is ignored and absent from a fresh checkout. No game modules/class
constructors, real save, executable or compiled kernel are modified.

Sixteen comparisons cover automatic/current/future starts, same-team renewal,
ordinary/short reserve terms, status aliases and AI hierarchy, one/three-entry
teams, scheduled capacity, returned loans, unregistered candidates and bonus
conversion. They compare stored native dates, duration, pay, role/status and
bonuses, and check that preparation leaves all fixture driver/team/query-cache
and regulation data unchanged. Eligibility and unrelated presentation/team-change
effects use fixture stubs. This is not live signing or full caller acceptance.

Two cheap portable checks cover ownership/effect isolation, invalid context data,
stale state and compatibility reporting without importing the game. There are no
new CI jobs/matrices, runner builds, API versions or public registrations.
