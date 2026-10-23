# Shared personnel negotiations

Driver and staff negotiations use many of the same game functions. This package
gives `PersonnelAdapter` one shared set of wrappers for offers, deadlines, AI
choices and player confirmations. It handles the game's records and presentation;
rule selection and mod checks belong to the
[regulation adapter](../regulations/README.md).

## Main parts

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

## Decisions and feedback

A mod refusal keeps a player confirmation pending. A refusal from the game's own
signing method retains its normal handling of alternative offers. If a required
mod check cannot run, the caller stops rather than treating that failure as an
ordinary rejected offer. Completed signings are not undone.

[feedback/](feedback/) uses the [shared UI components](../ui/README.md) to show
refusal dialogs and retain the reason in the scrollable Latest Response panel.
Its screen wrappers are separate from signing checks.

Driver and staff proposal calculations stay in their respective packages.
Accepted future contracts are not checked again when loaded or activated.
Importing this package installs nothing; it exposes no game-function API to mods.
