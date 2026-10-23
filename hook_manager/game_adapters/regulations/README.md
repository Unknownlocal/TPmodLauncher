# Regulation checks in the game

This package connects game actions to mod-written regulation checks. It prepares
a description of a proposed contract or development project, asks the launcher
for the required decisions, and controls whether the game's original action runs.
These are private launcher adapters, not a direct game-function API for mods.

## Main parts

- [personnel_adapter.py](personnel_adapter.py): `PersonnelAdapter` connects driver
  and staff signing to shared negotiation handling.
- [academy_adapter.py](academy_adapter.py): `AcademyAdapter` connects academy
  signing, renewal and their player or AI callers.
- [project_adapter.py](project_adapter.py): `ProjectAdapter` connects aero and
  chassis projects and their callers.
- [staffing_coordinator.py](staffing_coordinator.py): `StaffingCoordinator` checks
  complete team-staffing plans before applying their prepared changes.
- [guards.py](guards.py): `RegulationGuards` manages checks around the game
  functions selected by these adapters.
- [channel.py](channel.py): `RegulationChannel` carries requests, decisions and
  policy updates over an authenticated connection to the launcher on the same
  computer. The game waits for its reply.

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

Personnel checks cover new signings and renewals, including future-start
contracts. Once accepted, agreements load and take effect through the game's
normal code without a second mod approval.

Adapters check game method names and accepted arguments before installation.
Incompatibility is reported in command-line output and logs, and the affected integration
is skipped. Failed installation restores changes already made. Importing these
modules installs nothing; installation and removal belong to trusted launcher
code. Mods cannot choose native targets or call the private callable adapter.
