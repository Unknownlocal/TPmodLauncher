# Shared native personnel negotiation support

Driver and staff employment share the game's offer execution, uncontested signing
and player confirmation methods. This package supplies one caller catalogue and
one `PersonnelCallers` instance for those methods, preventing competing wrappers.
It imports no regulation dispatcher, mod definitions, launcher or sandbox runtime.

The consuming `PersonnelAdapter` lives under `../regulations/`. It owns rule
selection and validation; this package owns native preparation and cancellation.
Driver-specific query isolation remains in `../drivers/queries.py`. Common contract
formatting remains in `../employment.py`; the two native planners stay separate.

`caller_targets.py` includes the shared methods plus driver-specific proposal and
query methods when driver support is active. `preparation_methods.py` checks the
helpers that determine which data native queries may write. Staff commitment and
prestige-skill queries are checked read-only; team prestige getters can initialize
fields, which `preparation.py` holds until signing is allowed.

`callers.py` copies only offer/context data, preserving aliases. The staff branch
can also set `_oval_target_entry_id` even in Formula mode: when such a hint is
present, it uses a shallow staff copy for the pre-sign caller. The signing guard
restores the real staff argument, then publishes the hint only on allowance.
An ordinary native precheck failure retains the same hint/getter initialization
the game would have performed. Mod refusal discards them.

Player acceptance treats mod refusal differently from native `False`: native
`False` tries backups and closes the decision, while refusal must return its reason
with the decision still pending. `PersonnelCancellation` unwinds only that checked
acceptance path. Explicit declines retain native cleanup and backup behavior.

Enclosing market batches, recovery, takeover/merge, load/activation/rollover and
academy behavior remain separate coverage gates. This package installs nothing
on import and grants no callable-target access to mods.
