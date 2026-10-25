# Regulation checks in the game

This package connects game actions to mod-written regulation checks. It prepares
a description of a proposed contract or development project, asks the launcher
for the required decisions, and controls whether the game's original action runs.
These are private launcher adapters, not a direct game-function API for mods.

## Main parts

- [personnel_adapter.py](personnel_adapter.py): `PersonnelAdapter` connects driver
  and staff signing to shared negotiation handling. It also installs the common
  Formula staffing entries when their planner and signatures are compatible.
- [academy_adapter.py](academy_adapter.py): `AcademyAdapter` connects academy
  signing, renewal and their player or AI callers.
- [project_adapter.py](project_adapter.py): `ProjectAdapter` connects aero and
  chassis projects and their callers.
- [staffing_coordinator.py](staffing_coordinator.py): `StaffingCoordinator` checks
  complete team-staffing plans before applying their prepared changes.
- [guards.py](guards.py): `RegulationGuards` manages checks around the game
  functions selected by these adapters.
- [native_rules.py](native_rules.py): installs the fixed rookie-length and staff
  prestige switches from admitted booleans. Missing settings keep the
  game's checks. Trusted setup owns the returned hooks and their removal.
- [channel.py](channel.py): `RegulationChannel` carries requests, decisions and
  policy updates over an authenticated connection to the launcher on the same
  computer. The game waits for its reply.
- [native_policy.py](native_policy.py): `NativeRegulationPolicy` prepares the
  subscribed choices from the career's loaded regulations. It sends this data
  once, then uses the game's own method to choose a rule for each proposal's
  season. Later checks send small references to the prepared data.

The sibling [driver](../drivers/README.md), [staff](../staff/README.md),
[personnel](../personnel/README.md), [academy](../academy/README.md) and
[project](../projects/README.md) packages handle game calculations and records.
The launcher's `launcher/regulations` package owns regulation definitions and
stored rule data. Mod Python runs separately inside the Wasm sandbox, without
direct access to live game objects.

## How a check works

A planner calculates proposed terms without changing the live career.
[contexts.py](contexts.py) adds the action type, who initiated it and the rule
scope. Each action shares one proposal across the mods registered to check it.
It uses the regulations adopted for the season that the proposal concerns.

[outcome.py](outcome.py) keeps three results distinct:

- `allow`: all required mod checks permit the action. The game's own checks can
  still refuse it.
- `deny`: a mod rule refuses the action and supplies a reason.
- `unavailable`: required validation could not complete, so the action stops.

Messages have size limits, and preparation and communication share one deadline.
Policy updates replace stored rule choices before actions use them; they do not
resend all regulation data with every check. Repeated, unchanged personnel
refusals can be reused within one operation. Successful signing permission is
never cached.

## Applying changes

Staffing plans apply only when required roles are filled, their game inputs are
unchanged and the same policy still applies. Coordinated staffing and ordinary
signing share a reservation so they cannot change the career at the same time.
The coordinator supports the main thread and explicitly authorised game workers.
The mapped staffing wrappers grant a worker access only for that call and retain
any grant it already had. They preserve the game's list/None returns. A refused
batch discards all its private changes, including earlier allowed signings and
generated people; an unfinished batch does not leak a partial success list.
Loading saved agreements and activating accepted future deals stay native.

Personnel checks cover new signings and renewals, including future-start
contracts. Once accepted, agreements load and take effect through the game's
normal code without a second mod approval.

Adapters check game method names and accepted arguments before installation.
Incompatibility is reported in command-line output and logs, and the affected integration
is skipped. Failed installation restores changes already made. Importing these
modules installs nothing; installation and removal belong to trusted launcher
code. Mods cannot choose native targets or call the private callable adapter.

The private `AdapterModules.install_regulations` method owns the signing and
project adapters and native switches for a career supplied by trusted setup.
Private preparation takes its career from the game's loading attempt captured
by `CareerPreload`. It checks the attempt number, screen and loading job before
and after publishing data. Installation and activation check that same owner;
there is no separate current-career discovery or direct-install fallback.
Preparation uses `NativeRegulationPolicy` to choose rules. The installed adapters
keep those choices until removal. Setup reports which connections succeeded; this does not
mean the mod checks are ready to run. `staffing` coverage requires both the
joining-team and missing-role wrappers; loading, season rollover and head-of-aero
recovery reach these common methods. A changed entry or unsupported planner
leaves staffing coverage unavailable, even if ordinary signing still installs.
If validation is active, removal leaves the installation intact for a later
retry. Once idle, it restores the game methods it changed and closes their
connection before unloading the definitions.
Failed setup uses the same cleanup path; status reports `cleanup_pending` while
that cleanup is deferred.

Rule choices must be prepared again if the adopted choices, career season,
native manager or rule definitions are replaced.
The current choice's season records when setup observed it in force; native
saves do not record its original adoption year. Upcoming dates use the native
record, or the following season when the record has no date.
Setup skips saved replacement choices, such as custom future calendar actions,
whose data cannot be resolved by the loaded option name alone. These cases are
reported through the same compatibility message.

The native switches only enable or disable their named checks. Custom restrictions
use ordinary validators and the regulations adopted for the proposed contract's
season. Driver proposals include the original game's rookie-check applicability,
even when its native cap is disabled. Setup does not declare a policy ready for
use. The normal launcher does not activate these hooks.
