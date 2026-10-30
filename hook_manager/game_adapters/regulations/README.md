# Regulation checks in the game

This package connects game actions to mod-written regulation checks. It prepares
a description of an approach, proposed contract or development project, asks the launcher
for the required decisions, and controls whether the game's original action runs.
These are private launcher adapters, not a direct game-function API for mods.

Contract setup checks driver, staff, staffing and academy support together before
assigning any of their wrappers. A compatibility failure reports one reason and
leaves the whole personnel group native. An installation error restores owned
wrappers; development-project integration has its own compatibility checks.

## Main parts

- [regulation_approach.py](regulation_approach.py):
  `RegulationApproachAdapter` connects declared
  `personnel.approach` validators to the personnel mapping. It checks owned team
  approaches before native writes, including workers and shared negotiation
  drafts. The existing channel and in-memory policy handle all subscribing mods;
  native screens display the returned reason.

- [regulation_personnel.py](regulation_personnel.py):
  `RegulationPersonnelAdapter` connects driver
  and staff signing to shared negotiation handling. It also installs the common
  Formula staffing entries. Submitted offers use the same contract validators
  with `operation: "preview"` before native writes; actual signing uses
  `operation: "commit"`. Both prepare terms through the same native planners.
- [regulation_academy.py](regulation_academy.py):
  `RegulationAcademyAdapter` connects academy
  signing, renewal and their player or AI callers.
- [regulation_project.py](regulation_project.py):
  `RegulationProjectAdapter` connects aero and
  chassis projects and their callers.
- [staffing_coordinator.py](staffing_coordinator.py): `StaffingCoordinator` checks
  complete team-staffing plans before applying their prepared changes.
- [guards.py](guards.py): `RegulationGuards` manages checks around the game
  functions selected by these adapters. Personnel, academy and project adapters
  share its installation steps; a failure restores their owned wrappers.
- [regulation_native_switches.py](regulation_native_switches.py): installs the
  fixed rookie-length and staff prestige switches from booleans or references
  to the mod's own options.
  Missing settings keep the game's checks. Trusted setup owns the returned
  hooks and their removal.
- [channel.py](channel.py): `RegulationChannel` carries requests, decisions and
  complete career installs over an authenticated connection to the launcher on the same
  computer. It checks every decision in a reply before the guards select a refusal
  or allow the game action. The game waits for its reply.
- [regulation_policy.py](regulation_policy.py): `RegulationPolicy` prepares the
  reachable options for subscribed rules from the career's loaded regulations.
  It sends this data once, then uses the game's own method to choose an option
  for each proposal's season. Later checks send small references to that data.
- [regulation_category.py](regulation_category.py):
  `RegulationCategoryAdapter` adds enabled
  mods' prepared categories when the game loads definitions for the selected
  Formula pack. The game loads its own category definitions first, then uses the
  combined definitions to initialise rule choices.
- [regulation_scripts.py](regulation_scripts.py):
  `RegulationScripts` keeps enabled mods'
  scheduled rule choices when the game's change manager is created or restored.
  It supplies merging and checks to the shared regulation change adapter.
  These are JSON declarations; the game schedules and applies them.
- [regulation_change.py](regulation_change.py):
  `RegulationChangeAdapter` owns the shared construction, restore and save hooks.
  It keeps private automatic progress with each native manager, including when
  no automatic mods are enabled.
- [automatic_progress.py](automatic_progress.py): `AutomaticProgress` checks the
  saved season records and undelivered notices. Bounded data in an unknown or
  invalid format is retained for saving and reported as unavailable for decisions.
- [automatic_announcements.py](automatic_announcements.py): `AutomaticAnnouncements`
  sends ordinary native regulation mail using category and option labels. Each
  message identifies its category and decision season. Only confirmed insertion
  removes a pending notice; delivery errors do not stop other notices or rules.
- [automatic_changes.py](automatic_changes.py): `AutomaticChanges` uses prepared
  chance and week settings to choose changes once per category and season. Each
  rule has its own repeatable draw, leaving the game's random sequence alone.
  Automatic categories are excluded before native voting quotas are filled.
  Native scripts and approved changes keep their slot. The game records chosen
  options for next season; the helper records the checked season and the notice
  awaiting delivery.
- [regulation_native_defaults.py](regulation_native_defaults.py): reads built-in
  category defaults from the game's fixed definition module, including the
  compound default for the selected pack. It waits for ongoing imports before loading that module, then
  returns copied data without constructing a career or installing hooks.
- [regulation_screen.py](regulation_screen.py):
  `RegulationScreenAdapter` appends loaded categories
  missing from the native screen's groups under Additional regulations. It uses
  native cards, selection, option inspection and scrollbars. Automatic cards show
  their configured chance; their details show chance and announcement week using
  the native metric widgets and palette. Other categories keep their native text.
- [regulation_selection.py](regulation_selection.py): `RegulationSelection`
  reads an option's boolean using the game's season selector. Offer preparation
  and signing share the proposed season; nested calls restore their previous
  season afterwards. Queries without a date use the native offer-start inference
  for rookies, or the current season for staff prestige.

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

Future chassis contexts include the native operational technical profile under
`proposal.technical_regulation`. Its era, severity and effects follow the game's
announcement behaviour. The project adapter prepares it once on private inputs
and checks for relevant changes before accepting the mod replies. Aero/spec and
current chassis proposals omit that field. Rule option data remains separate
from these calculated development inputs; mod code owns any resulting refusal.

[outcome.py](outcome.py) keeps three results distinct:

- `allow`: all required mod checks permit the action. The game's own checks can
  still refuse it.
- `deny`: a mod rule refuses the action and supplies a reason.
- `unavailable`: required validation could not complete, so the action stops.

Messages have size limits, and preparation and communication share one deadline.
Each career installs all reachable rule choices before actions use them. Later
checks select those stored choices without resending their data. A new career
requires reset and a fresh complete install. Repeated, unchanged personnel
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
Incompatibility is reported in command-line output and logs, and the affected
integration is skipped. Failed installation restores changes already made.
Importing these modules installs nothing; trusted launcher code owns installation
and removal. Mods cannot choose native targets or call the private callable adapter.

The private `AdapterModules.install_regulations` method owns the signing and
project adapters and native switches for a career supplied by trusted setup.
Private preparation takes its career from the game's loading attempt captured
by `CareerPreload`. It checks the attempt number, screen and loading job before
and after publishing data. Installation and activation check that same owner;
there is no separate current-career discovery or direct-install fallback.
Before career selection opens, trusted setup can install the category loader.
Each native load receives independent custom category data. Setup checks that
the career received it and the loader is still owned before publishing policy.
The loader stays with the pack between careers; full cleanup waits for an active
load before restoring it. Other packs and Oval careers keep native definitions.

The regulation screen adapter stays installed with the category loader while
the pack is loaded. It waits for the screen's normal import without holding up
career loading. Cards use the loaded category labels and option descriptions;
drawing does not reread
JSON or run validators. Inspecting an alternative does not change the adopted
rule. A UI compatibility failure leaves validation active and reports through
the next ordinary command-line/log update. Full cleanup restores the owned
drawing method; leaving a career keeps it installed.

On creation or restoration, enabled categories are checked against saved
definitions. Missing categories are added; differing saved definitions are
reported rather than replaced. Existing rule choices are kept.
When custom scripts are present, the shared regulation change adapter merges
missing scripts into the loaded policy, using the game's own policy validation.
A matching saved script is kept once; a changed script or category
reports incompatibility and prevents mod activation. Saved progress remains
native, including completed and suppressed script IDs. The loaded voting or
scripted mode is preserved. Script merging uses the shared regulation change
hooks; category-only mods add no script work.

The bridge observes the game's change-manager import and installs its three
shared load/save hooks together. They outlive mod validation and category loading,
so disabling every mod or losing the shared runner does not discard saved progress.
Cleanup waits for native loads and saves before restoring these hooks. Cached
stopped wrappers pass directly to the original game methods.

The optional `mod_automatic_changes` field sits inside the game's existing
`formula_regulation_governance` save object. It records the last checked season
per category and only undelivered announcements, retaining their original season
and option. Ordinary native choices, reviews and ballots remain in their existing fields. Missing
metadata starts empty, and each career has an independent copy. Saving copies
cached plain data and never calls a mod. Prepared weekly hooks belong to the
category loader and are removed with it; shared persistence stays installed.
They check room before staging and recover a completed native decision by its
saved identity, even if native staging finishes and then raises an error.
Inconsistent saved records are reported without repeating staging.
Updating progress measures the changed
record rather than encoding all saved progress.

Trusted setup installs automatic policies before career loading and checks the
saved definitions, progress, weekly hooks and inbox before activation. The
launcher requires explicit automatic-change coverage. The existing channel
readiness gate prevents weekly decisions and delivery during setup, retirement
or failed validation. Retirement keeps the pack's wrappers but waits for any
weekly operation already running before allowing another career to bind.

Pending notices retry before new decisions, including before the configured
week in a later season. Old delivery failures do not replace newer notices.
Mail uses absolute effective seasons, so a delayed notice remains accurate after
adoption. The inbox is checked even when native sending raises after insertion.
Once a notice is confirmed, deleting that email does not recreate it. Inactive
categories keep saved progress without sending notices or making decisions.

The private field is limited to 1 MiB, eight levels below its root and 32,768 values.
The supported format permits 128 categories and 32 pending notices per category.
Invalid or newer formats are reported and retained when within those bounds.
Data that cannot be copied safely causes a save error rather than being lost.
A vanilla game re-save can omit the field because these hooks are then absent.

Preparation uses `RegulationPolicy` to choose rules. The installed adapters
keep the prepared option data until removal. Setup reports which connections
succeeded; the mod checks still need activation. `staffing` coverage requires both
the joining-team and missing-role wrappers; loading, season rollover and head-of-aero
recovery reach these common methods. A changed entry or unsupported planner
leaves staffing coverage unavailable, even if ordinary signing still installs.
If validation is active, removal leaves the installation intact for a later
retry. Once idle, it restores the game methods it changed and closes their
connection before unloading the definitions.
Failed setup uses the same cleanup path; status reports `cleanup_pending` while
that cleanup is deferred.
Career retirement uses the same idle check to remove its hooks and policy while
retaining the loading observer and connection. It clears stored career data
through the existing reset operation. The next career must publish its data and
confirm hook coverage before activation; a cancelled setup cannot pass approval
to another save.

Ordinary adoption, scheduling and season rollover use fresh current/upcoming
references into the options already stored. They do not resend option data or
replace hooks. The game's own selector decides which option applies to a
proposal's season. Checks compare the small season/choice inputs before and
after a reply; staffing compares them again before publishing its full batch.
A change refuses that attempt without disabling healthy validators.
Replacing the native manager or rule definitions still requires new preparation.
The current choice's season records when the check observed it in force; native
saves do not record its original adoption year. Upcoming dates use the native
record, or the following season when the record has no date.
Setup also stores a calendar action already present in a loaded save. Its
operational values have their own reference when they differ from the base
option with the same name. Rollover can remove the action without resending
data. Native staging replaces the whole saved action; a new action object needs
new preparation. Other saved replacement families and ambiguous current/future
values sharing one key are skipped with the compatibility message.

The native switches only enable or disable their named checks. Custom restrictions
use ordinary validators and the regulations adopted for the proposed contract's
season. Driver proposals include the original game's rookie-check applicability,
even when its native cap is disabled. Setup does not declare a policy ready for
use. The launcher activates the captured loading attempt only after confirming
complete coverage for its required checks. The fixed installation request
carries checked native-switch booleans or owned option references. A reference
reports `regulated` only after its wrapper is installed. A requested bypass
must report an installed wrapper before activation; an incompatible switch keeps the
pack unready and setup restores the hooks it installed.
After an owner fails, `restore-native-rules` removes only bypass helpers and
closes the validation gate. Ordinary signing/project guards remain installed
so unavailable custom checks still refuse actions.

Automated negotiation callers admit the game's weekly worker while they run.
Nested offers keep that permission; leaving the caller removes any permission
it introduced. Direct signing from an unrelated worker remains refused. A
personnel validation error keeps its cause in the adapter and sends it to the
next CLI/log update. The game retains the short Validation unavailable message.
