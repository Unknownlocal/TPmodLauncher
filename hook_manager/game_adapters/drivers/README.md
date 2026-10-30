# Driver employment support

This package describes proposed Formula driver contracts without signing them or
changing the live career. It works out the terms that mod rules will check;
the game's signing method still decides whether an allowed proposal succeeds.

## Main parts

- [planner.py](planner.py): `DriverContractPlanner` builds the proposed contract,
  including its team, role, status, dates, salary and bonuses. It also describes
  the prior agreement and distinguishes a signing from a renewal.
- [snapshot.py](snapshot.py): copies the candidate and relevant team contracts so
  game helpers can calculate dates and status without editing live records.
- [queries.py](queries.py): protects live contract records and the signing-query
  cache when game callers ask about eligibility or contract status. Frequent
  active-contract reads pass arguments directly rather than rebuilding them.
- [query_inputs.py](query_inputs.py): reuses private active-contract inputs within
  one negotiation. Every lookup still checks its requested week. Returned
  agreements leave the reuse store so caller edits cannot affect later reads.
  Drivers without current contracts share one private empty input.
- [terms.py](terms.py): handles returned academy loans on the working copies.
  Common person and contract formatting lives in
  [employment.py](../employment.py).
- [rookie_check.py](rookie_check.py): `RookieContractLengthSwitch` can disable the
  native rookie length cap for one career. Missing or true installs nothing;
  false supplies the game's no-cap result. Removal restores the original helper.
  Trusted setup can supply a boolean query instead. The regulation consumer
  chooses the applicable contract season; this switch only checks its result.

The planner accounts for current and future seats, reserve cover, loan returns,
bonus conversion and the status promised within a team. These calculations stay
separate from staff employment because the game's rules differ.

## Integration

[Shared personnel support](../personnel/README.md) handles offers, AI negotiations
and player confirmations. The [regulation adapter](../regulations/README.md)
selects the rules, checks the proposal and controls whether signing runs.
Already accepted contracts take effect through the game's normal activation code.
Working copies are discarded when signing is allowed and when a caller finishes;
they are never carried into another negotiation.

Driver proposals include
`proposal.native_checks.rookie_contract_length_applies`. The original game helper
decides this result, including its experience, role and employer checks, even
when the native cap is disabled. It describes this proposed contract, rather
than declaring the driver a rookie in every situation. A working manager skips
team ranking, which only chooses the unused native cap and can initialise live
engines. The planner does not copy the whole career.

Custom validators can use that boolean with their adopted regulation data to
enforce their own contract limits. The launcher connects declared contract
validators and native switches when preparing the selected career.

Compatibility checks use game method names and the arguments they accept.
Importing this package installs no hooks or game modules. It contains private
launcher support, with no direct game-function access for mods.
