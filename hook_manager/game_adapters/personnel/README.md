# Shared personnel negotiations

Driver and staff negotiations use many of the same game functions. This package
gives `RegulationPersonnelAdapter` one shared set of wrappers for offers,
deadlines, AI choices and player confirmations. It handles the game's records and presentation;
rule selection and mod checks belong to the
[regulation adapter](../regulations/README.md).

## Main parts

- [payroll.py](payroll.py): the internal `project_payroll` helper reads annual
  staff and driver salaries from supplied contract records. It includes a
  proposed agreement, accounts for replacements and scheduled deals, and returns
  the totals and the week they describe without changing either roster.
  Requested totals are checked again before allowing a write. Staffing also
  checks the borrowed driver records before applying the completed batch.
  These checks last only for that operation; they do not cache the career.

- [offer.py](offer.py): `PersonnelOffer` translates a submitted offer into the
  existing planners' signing arguments. It preserves native salary/bonus field
  choices without calling a signer or changing a negotiation.

- [approach.py](approach.py): `PersonnelApproach` maps the shared native approach
  method and prepares person, team and intended-start data. It uses private
  driver contracts for native date queries and returns native refusal triples.
  It contains no regulation conditions or mod dispatch.

- [callers.py](callers.py): `PersonnelCallers` wraps the shared negotiation
  functions. [caller_targets.py](caller_targets.py) identifies those functions,
  and [preparation.py](preparation.py) holds temporary changes made by their
  scoring and eligibility helpers.
- [market_preparation.py](market_preparation.py): `MarketPreparation` keeps draft
  offers, deadlines, pending confirmations and mail separate from live records.
  Completed market actions can be applied as the batch progresses; a later
  interruption discards the remaining drafts.
- [market_graph.py](market_graph.py): `MarketGraph` copies related records together
  so references to the same offer still refer to one offer. Applying changes
  preserves the game's original dictionaries and lists.
- [market_mail.py](market_mail.py): `MarketMail` keeps messages with the offer that
  produced them, so unfinished work does not send premature mail.
- [cancellation.py](cancellation.py): `PersonnelCancellation` carries a refusal
  back to the caller responsible for the action.
- [rollover.py](rollover.py): grants worker access for the active career while
  the game's season-end function performs its normal work.
- [native_operation.py](native_operation.py): keeps worker permission within
  identified game calls and carries unavailable validation back to their owner.

## Decisions and feedback

Ordinary and final offer submissions are checked before the native method
changes their record or decision deadline. Refusals return the native
false/message/record result, so each screen uses its normal submission error
display. Screen-local attempt handling remains native. The later signing
check is separate and does not reuse a submission allowance.

A mod refusal keeps a player confirmation pending. A refusal from the game's own
signing method retains its normal handling of alternative offers. If a required
mod check cannot run, the caller stops rather than treating that failure as an
ordinary rejected offer. Completed signings are not undone.

Season-end signing and academy AI calls can run on the game's background worker.
Their wrappers grant access only during those identified calls and release it
afterwards. If required checks become unavailable, the operation stops with an
error handled by the game's worker. Catching that error inside a native helper
does not turn the unfinished operation into a success. Ordinary rule refusals
retain native candidate selection.

[feedback/](feedback/) uses the [shared UI components](../ui/README.md) to show
refusal dialogs and retain the reason in the scrollable Latest Response panel.
Its screen wrappers are separate from signing checks.

Inbox contract approvals use the same refusal callback. `InboxNotice` shows the
reason and candidate in the shared dialog while the agreement remains pending.
Only the current Accept action receives that feedback; declining and trying
backup offers keep their normal handling. The inbox retains its own action
results and sound cues. Dialog input stays within the dialog until it closes.
Screen compatibility failures disable only that screen's optional feedback;
contract validation remains active.

Staff approach failures reuse that dialog with approach-specific wording while
keeping native inline text and error sound. The adapter observes the native
confirmation's outcome only during that call; it restores the sound helper
afterwards. Approach-only mods own the same optional feedback through
their approach adapter. Driver approaches keep the native failure dialog, paired
with the native action-error cue when it opens.

The driver and staff packages prepare their own contract terms.
Accepted future contracts are not checked again when loaded or activated.
Importing this package installs nothing; it exposes no game-function API to mods.
