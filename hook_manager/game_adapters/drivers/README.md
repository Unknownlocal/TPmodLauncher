# Native driver support

This private package maps Formula driver employment and prepares final contract
terms without signing or changing the career. It also supplies checked caller
preparation, query isolation and cancellation behavior for trusted guard users.
It imports no regulation dispatcher, mod definitions, launcher or sandbox runtime.
Importing it installs no hooks and forces no game imports.

The inspected executable was
`e1812f2fce94c16e02df8056f62ef976e6c8d3b877101d3019fb77005059f773`.
This records inspection provenance only. Compatibility checks the signing
signature and relevant implementations/defaults, excluding source locations.
The mapping below was re-read from the current installed archive.

| Native path | Inspected behavior / later guard requirement |
| --- | --- |
| `DriverManager.sign_driver`, line 3954 | Chooses dates from current/future capacity, supports shortened reserve cover, normalizes bonuses/status, stores active or future employment and reconciles hierarchy. Loan-return branches can clear contracts at 4028/4043 before later validation; final assignment is at 4121/4126. The signing guard now runs before the whole body. |
| `PersonnelNegotiationManager.accept_player_driver_proposal`, line 1702 | Converts proposal fields and requests next-season signing. It handles `False` before accepted/viewed/save writes at 1766 onward. Entry-specific branches belong to Oval, outside this mapper. |
| `PersonnelNegotiationManager._execute_offer`, line 4136 | Normalizes rookie offer duration before signing. It returns the signing boolean and records a final-validation reason on refusal. Enclosing offer resolution still needs transaction/caller checks. |
| `submit_offer`, line 2182; `ai_manage_drivers`, line 3435 | Offer/market flows converge through negotiation execution. A submitted offer is not a final contract, and incomplete approaches cannot authorize signing. |
| `GameState._activate_future_driver_contracts`, line 11939 | Activates contracts directly during week advancement, bypassing signing. It needs enclosing transaction preflight rather than a signing-only guard. |
| `GameState.finalize_and_rollover_season`, line 13396 | Contains fallback signing and direct active/future assignments. Return values, roster recovery and decorator/worker mutations remain later acceptance work. |
| Initial/load/exit paths | `DriverManager.load_initial` 668 and `load` 832 restore records directly. Retirement, fatalities, firing and expiration remove agreements; status scheduling also modifies records outside signing. Restore/activation and reassignment coverage remain release gates. |

## Preparing terms

Construct `DriverContractPlanner(state)` after the ordinary native driver and
Formula-entry modules finish loading. Incompatibility raises; the consuming
adapter decides how to report and skip its integration. The regulation consumer's
`DriverAdapter.create` handles that CLI/log reporting and installation rollback.
It lives in `../regulations/driver_adapter.py`.

`prepare(arguments)` expects fully bound `sign_driver` arguments including
`self` and defaults. It returns owned `clock`, `effective` and `proposal` data:
person/employer identities, prior agreement and resulting role/status, dates,
weeks, annual salary and bonuses. It never calls signing or decides eligibility.
Regulation invocation metadata is added separately by `regulations/contexts.py`.

The snapshot copies the candidate and destination team's relevant contract
records. Native queries normalize only those copies and use no live signing
query cache. Newly generated candidates retain their actual roster membership.
Calendar/capacity helpers borrow native team entry plans and adopted native
regulation data read-only; no whole career or definition catalog is serialized.

Normalization follows the inspected native signing path:

- Automatic current/future dates depend on the candidate's active agreement and
  current seats. The driver week includes the native season-start offset.
- Player reserve cover can end at next season instead of lasting a full year.
- Returned academy loans are removed on copies before date queries; the prior
  agreement is retained. Future-loan removal takes precedence over current loans.
- A fresh owned input cell runs the checked nested native bonus converter.
- Native post-signing hierarchy can change promised status. Helpers calculate
  that on private agreements while suppressing later mail/morale effects.

The current agreement takes precedence for `existing`, falling back to a future
record only when no current agreement exists. Same-employer continuation is
`renew`; other employment is `sign`. `person.role` can be null independently of
`proposed.role`. Native records store weeks, so separate season-count/retainer
fields remain null. New salary must be finite numeric data; absent saved-record
fields remain null. Entry-specific/Oval terms remain outside this Formula mapper.

## Preparing callers

`DriverCallers` accepts a trusted guard lifecycle; it handles player proposals,
uncontested signing, offer execution and player confirmation. Native query
wrappers keep agreement/status/cache edits private before signing. Scoring and
prestige getters can also fill personality/career-stage and team prestige/history
fields. `preparation.py` holds only those small writes until allowance, or an
ordinary native precheck failure; a guard refusal discards them. Native pure
personality conversion can still fill its detached value memoization cache.

Offer/context drafts preserve aliases and publish only inspected term/score
fields when there is no guard refusal. Refusal publishes the reason instead.
Player acceptance uses private cancellation before native backup/decision-closing
writes. Native failures and explicit declines keep native fallback behavior.
The consumers own subscriptions, dispatch and the decision to allow or deny.

The wider market-resolution batch, week/season activation, rollover, restore,
academy paths and staff coverage remain release gates for regulation integration.
See [the regulation consumer](../regulations/README.md) for current guard behavior.

Optional ignored `tools/regulations/check_driver_plans.py` and
`check_driver_guards.py` run actual inspected bodies on owned fixtures using game
Python 3.13. Sixteen term comparisons and twenty-one guard/caller checks cover
normalization, refusal isolation and native control flow. Owned configuration,
metric and presentation stubs delimit that evidence; no live save, game module
constructor, executable or compiled kernel is changed. Tools stay local and are
absent from a fresh checkout. Portable tests remain in the hook-manager suite.
