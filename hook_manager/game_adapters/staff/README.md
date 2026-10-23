# Staff employment support

This package describes proposed Formula staff contracts and prepares whole
team-staffing operations without changing the live career. It uses the game's
existing calculations; the [regulation adapter](../regulations/README.md) owns
mod checks and permission to apply changes.

## Main parts

- [planner.py](planner.py): `StaffContractPlanner` describes a contract with a team
  or engine supplier. It calculates the employer, role, dates, salary and bonuses
  using checked game helpers, without signing or running prestige checks.
- [staffing.py](staffing.py): `StaffingPlanner` runs the game's staff selection,
  generation and signing on temporary copies. It prepares staff for joining
  teams or fills missing team roles. Later choices see the contracts already
  planned during that run.
- [staffing_plan.py](staffing_plan.py): `StaffingPlan` holds the result, generated
  people, new history entries and any roles still unfilled.
  [staff_signing.py](staff_signing.py) records each attempted contract and its
  result from the game's signing code. That result is separate from mod approval.
- [staffing_publication.py](staffing_publication.py): `StaffingPublication` checks
  that the relevant career inputs still match, then applies prepared changes to
  the game's original people and containers.

## Contract and staffing flow

Individual contracts can start this season or next season. Cover for a vacant
role can last only for the remainder of the current season. Staff bonus handling
and employer selection differ from driver contracts, so those calculations stay
here; common record formatting lives in [employment.py](../employment.py).

Whole-team staffing uses the game's recovery path, which bypasses ordinary
prestige requirements. An optional validation callback checks each proposed
contract before signing changes the temporary staff records. Generated people remain
separate from the live roster until the result is applied.

`StaffingCoordinator`, in the regulation adapter package, checks that the plan is
complete and the policy unchanged before applying it. It shares a reservation
with ordinary signing to prevent overlapping career changes. Applying a plan
uses its prepared result; it does not replay the signing calls.

Compatibility checks use game method names and the arguments they accept.
Importing this package installs no hooks. Its planners and staffing records are
private launcher support and are not passed to mods.
