# Driver contracts

The driver adapter prepares Formula employment proposals for
`personnel.contract` validators. Use source API `0.4.0` with `regulations`.
Mods receive copied terms before signing; the game's remaining checks decide
whether an allowed proposal succeeds.

The [personnel API](../personnel/README.md) defines the shared identities,
contract fields, offer previews and optional payroll input. The
[regulation API](../regulations/README.md) defines declarations and handler returns.

## Data available to mods

`context["proposal"]` describes the driver, team, previous agreement and proposed
contract. `contract_kind` is `"employment"`; `action` is `"sign"` or `"renew"`.
Proposed terms include role, promised status, dates, annual salary and bonuses.
Money is in millions, and employment duration is in weeks.

The planner accounts for current/future seats, reserve cover, academy loan
returns and the game's driver hierarchy. Use these calculated terms rather
than assuming they equal the original offer.

The additional boolean
`proposal["native_checks"]["rookie_contract_length_applies"]` tells a validator
whether the game's rookie-length check applies to this person, role and team.
It is calculated even when the native length cap is disabled; it is not a
general label declaring the driver a rookie in all situations.

## Native rookie-length check

A mod's `native_rules` can set `rookie_contract_length_check` to `false` to
disable that one native restriction. Omitted or `true` keeps it. A reference
to a boolean in the mod's own category can vary this by contract season.

A validator can combine the applicability boolean with its selected rule to
enforce a custom limit. See the [rookie contract example](../../../examples/rookie_contract_mod)
and [native switch format](../regulations/README.md#native-rule-switches).

## Internal responsibilities

`DriverContractPlanner` uses small working copies for native date, seat and
status calculations. Query helpers protect live contracts while negotiations
run; `RookieContractLengthSwitch` owns the optional cap bypass.
[Personnel support](../personnel/README.md) handles negotiations and feedback,
while the regulation adapter selects rules and controls signing.

Working copies do not outlive the negotiation. Accepted future contracts take
effect through normal game code. These modules are private launcher support;
importing them installs no hooks.
