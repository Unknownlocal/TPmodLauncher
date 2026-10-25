# Regulation definitions and validation

This private package reads enabled mods' regulation definitions and prepares their
Python validation functions to run inside Wasm. The launcher manages loading,
rule data and execution. Trusted game adapters supply the places where checks
run. Mod code cannot select a game function or call the private callable engine.
Public source admission accepts API 0.2.0/0.3.0; regulation permission remains
private to the 0.4.0 interface.

## Definition preparation

`PackMods.prepare_regulations()` runs once after activation. Its owners are live,
accepted source mods with the `regulations` permission. Prebuilt components and
closed or failed source mods cannot add owners. Each eligible mod retains its
checked source copy until preparation finishes or fails, then releases it. The
guest retains its source for later imports. Preparation never reopens a changed
`src/` tree. Without eligible owners, this path reads no files.

`RegulationDefinitions.read()` checks the selected folder and reads its fixed
`regulations.json`, up to 1 MiB. A missing or unreadable file rejects preparation.
`from_bytes()` accepts bounded bytes and checked source copies. Trusted callers
supply mapped context names and any native fallback categories.

Normal startup supplies those fallback categories through a private, read-only
bridge request. Preparation requests them once, only when an enabled mod section
is present. The game supplies its built-in definitions and the compound default
for the selected pack's tyre model. The request waits for ongoing imports to
finish before loading its fixed regulation definition module. It has a ten-second
limit. File-defined categories keep precedence; fallbacks allow references
without being inserted into the file document.
The request checks the selected pack and copies at most 128 categories within
1 MiB. It constructs no career and installs no hooks. Compatibility failures
are reported through command-line output and logs, leaving native play available.

[`json_ingest`](../json_ingest/README.md) decodes UTF-8 and selects fields.
`converter.py` selects native data and enabled mod sections; `owner_converter.py`
checks each section and creates an `OwnerDefinition`. `RegulationSession` checks
collisions and combined limits. `RegulationDefinitions` stores the finished view.
It copies only the declarations needed for binding and native loading. Revision
IDs are returned in a separate copied map. The game reads its own native document;
the launcher retains only the category references needed by mod rules.

`custom_categories` supplies a separate copy of enabled mods' categories.
After handlers bind, normal startup sends those categories and the selected pack
folder through the fixed loading request. The game adapter adds them before
native code initialises defaults or restores saved selections. It checks the
loaded categories again before policy publication. No file is rewritten, and
there is no category-loading hook when no enabled owner adds categories.

`custom_scripts` copies enabled mods' scheduled rule choices into the same loading
request. These are JSON declarations, separate from Python validation functions.
The game adapter adds them after governance construction and saved-policy
restoration, before career setup can activate mod checks. The game validates the
combined policy and retains its saved script progress. Existing matching scripts
are reused; conflicting saved definitions prevent activation. Script execution
follows the loaded `change_control.mode`: adding scripts keeps that mode intact.
The prepared loading request allows up to 128 categories and 4,096 script rows
within 2 MiB, including the IDs prefixed with their mod names during preparation.
The original `regulations.json` file limit remains 1 MiB.

`PreparationIndex` reuses option lookups, source paths and data size checks
within one preparation. Owner data refers to native definitions without copying
the whole native catalogue. `payload.py` checks each owner's actual metadata
against its per-check limits; a shared base measurement cannot approve another
owner's extra data. Revision calculation streams checked declarations, exact
source text and referenced native data into SHA-256. Those digests identify rule
revisions; they do not check game-method compatibility. Preparation caches are
discarded on completion or failure.

## Selected JSON structure

The optional root `mod_extensions` object is keyed by manifest ID. An enabled
section requires integer `schema: 1` and permits `categories`, `option_data`,
`validators`, `scripted_changes` and `native_rules`. Disabled sections are left
unread beyond JSON syntax and the whole-file size check. Duplicate fields in
enabled sections are rejected; native fields use the last value, as in the game.

- Categories require `key`, `label`, `default` and `options`, with optional
  `description` and `neighbours`. Keys start with the owner ID and `.` and cannot
  collide with existing categories.
- Options require consecutive positive integer `id`, unique `key` and printable
  `name`. Optional fields are `summary`, `description` and object `parameters`.
  Neighbours name distinct other options in the same category.
- `option_data[category][option]` holds the mod's extra JSON object for a native
  or owned option. It does not overwrite native fields or contain executable
  operators.
- Validators have exactly `id`, `rule`, `scope` and `handler`. Rules are native
  or defined by the mod; scopes need trusted game adapters. Python modules named
  by a handler must exist in that mod's source. Function lookup happens inside Wasm.
- Scripts require `key`, `new_regulation` and positive `effective_season`, with
  optional `id`. `new_regulation` contains the option's `key`. Scripts target
  owned categories. IDs are owner-qualified; omitted IDs use declaration order.
  Native scripts are preserved; duplicate IDs or category/season pairs fail.
- `native_rules` accepts two boolean switches: `rookie_contract_length_check`
  and `staff_prestige_check`. Missing or true keeps the corresponding native
  check; false requests that it be disabled. Custom restrictions and seasonal
  maxima use ordinary validators and their regulation data. Each named
  native rule can have only one enabled owner; conflicts reject preparation.
  Declarations cannot name arbitrary game functions.

Native switches are checked and included in rule revisions. After career data
is acknowledged, `RegulationRuntime.native_rules()` supplies their values to
the fixed installation request. Activation requires each declared switch to
report `native` for true or `disabled` for false. A skipped incompatible switch
cannot become ready; failed activation restores the installed hooks.
Switch-only mods use an empty data publication and need no validation handlers.
If a required owner fails after setup, the controller closes transport and uses
the fixed `restore-native-rules` request to remove the bypasses. Ordinary action
guards stay installed and refuse unchecked actions; failure cannot remove a
custom restriction by reverting the whole installation to native behaviour.

`ValidationDispatch.native_rules()` copies the fixed booleans for private game
setup after checking the current career, publication and readiness. Publication
failure makes them unavailable; career reset cannot revive a failed policy.
The game keeps these values between lookups.

Driver contexts include `proposal.native_checks.rookie_contract_length_applies`.
The original game helper determines that boolean for the proposed person, role
and employer, even when the native length check is disabled. A mod validator can
use it with a maximum in its adopted rule data, without copying the game's
definition of a rookie. The [driver adapter](../../hook_manager/game_adapters/drivers/README.md)
skips the unrelated cap-ranking calculation so it cannot initialise live engines.

Limits are 32 owners, 32 categories/validators per owner, 32 options per custom
category, 128 categories/256 validators per session and 128 scripts per owner.
Each validator's complete context and rule together must fit 4,096 UTF-8 bytes,
1,024 nodes and eight container levels. These are invocation limits, separate
from the 1 MiB file limit. Failed preparation returns no partial owner set.

## Loading checks and keeping rule data in memory

`PackMods.bind_regulations()` creates a `RegulationRuntime`. It binds handler
tables once per shared source runner through `SharedHandlerRunner`. Each owner
keeps its own IDs, declarations and generations. Owners declaring data without
validators stay required but need no guest handler table. A group with one source
mod uses the same binding path.

Handlers are synchronous two-argument functions from the accepted owner source.
Functions may be re-exported within the mod's checked source; mod Python is never
imported into the launcher.
Binding uses the source-import allowance: 256 million fuel units and five seconds
per owner. Calls return `{"decision":"allow"}` or
`{"decision":"deny","message":"Reason"}`. Refusals need 1-160 trimmed printable
characters. Conditions stay in mod code.

The [validation package](validation/README.md) owns the combined catalogue,
updates and reference-based dispatch. Rule data stays in memory between actions.
Shared owners reuse native records, and each proposal table transfers once per
runner. Every handler still receives fresh ordinary JSON arguments.
All reachable options are acknowledged at setup. Ordinary native adoption and
season changes therefore need only fresh choice references in each action,
without another publication or handler binding. The game adapter rejects a
reply or staffing batch if its selection inputs changed during validation.

Up to four invocations share a preparation allowance of 64 million fuel units.
Each handler has its own five-million fuel and 64 host-call allowance. The whole
action shares its original absolute 100 ms deadline, including transfer and
cleanup. No new phase restarts that deadline. Failure closes the affected Store;
all source peers in it become unavailable. The private exports use no lifecycle
events and expose no generic callable API.

## Transport and readiness

`RegulationServer` owns one authenticated loopback connection and a bounded queue.
Its I/O thread receives requests and wakes the controller; only controller
`poll()` enters Wasm. `RegulationChannel` uses protocol v2, session tokens,
request numbers, career numbers (`epoch`) and acknowledged rule-data versions.
Socket data cannot supply Python code or native function targets.

`PackMods.prepare_native_regulations()` uses the bridge's fixed preparation
request to publish the loaded career's rule data before any game hooks install.
While waiting for the game's reply, the bridge services regulation requests on
the controller thread. Mod cleanup defers UI updates until that reply arrives,
so it cannot start a second bridge request during preparation. Publication alone
does not approve hook coverage.

`PackMods.activate_native_regulations()` installs hooks with validation held off,
checks the installation report, then asks the game to enable them. Readiness and
mod UI remain held until that final reply. Failure closes transport and requests
native cleanup. Healthy validators are not permanently disabled by a temporary
refusal during installation. These fixed controls cannot name arbitrary methods.

Normal startup binds admitted handlers once and arms `CareerPreload` before
enabling career selection. Mod UI still waits for regulation readiness.
The bridge reports changes to the captured loading attempt; `PackMods` prepares
and activates it on the controller thread. Repeated or older waiting notices do
not repeat setup. No enabled declarations means no native loading hooks.
Setup failures use the controller's normal cleanup, restoring native hooks and
reporting the failure in command-line output and the log. Native departure
notices retire career hooks after admitted work finishes. The connection and
handler tables stay loaded; the next career must publish fresh data and confirm
coverage again. A cancelled attempt can retire data published before activation,
without closing the pack's interpreter or validators. Stale notices cannot retire
a later prepared career.

`publish(native=..., custom=...)` streams changed rules through `policy-begin`,
ordered `policy-page` rows and `policy-seal`. Native publications supply complete
option data for subscribed native rules; custom publications contain admitted
option keys. `None` keeps installed options while advancing their generation.
Private overlays stay in the launcher. One control deadline covers all pages and
commits. Readiness stays blocked until commit acknowledgement. `reset(epoch)`
clears policy while keeping
bindings; a new publication and native coverage approval are required and a
failed Store cannot be revived. Data publication can precede hook approval.
Reset may retire a complete publication before activation when loading is
cancelled; actions still require approved hooks.

Actions carry shared contexts/selections and owner binding-slot references.
Frames are bounded to 256 KiB. Larger actions use ordered base64 fragments, up to
8,912,896 decoded bytes/46 parts, assembled before parsing. Large refusal results
also use bounded fragments. All fragments share the original action deadline.
An identified group failure can keep the connection open; broken transport makes
every required owner unavailable. There is no reconnection or stale approval reuse.
The server has a private wakeup connection so shutdown can interrupt an idle read
even while the native client remains connected; it needs no idle polling loop.

`RegulationRuntime` retains required owner identities even after pack cleanup
removes their live entries. Failure expands to every required owner sharing the
Store, including definition-only owners. Wire failure replies name only the
execution owners advertised to the native channel. A connection alone is not
readiness: trusted setup must report every required part of the installed game
hooks, and execution owners need committed policy. `personnel.contract` requires
driver, staff and academy signing plus staffing operations;
`development.project` requires development projects. A skipped or missing part
keeps the pack unready. Extra parts do not block packs that do not use them.
An unknown scope is rejected before handlers bind. The component report comes
from trusted setup, never from mod declarations.

`PackMods` reserves eligible rule owners before any source import or activation.
Failure does not release those reservations. Desired UI stays staged while rules
are unready; published UI is cleared on failure. The menu may open once native
loading protection is armed, while regulation readiness still waits for the career.
Cleanup removes all failed peers' declarations before publishing replacements.
During validation it defers native UI updates until the reply has been released,
so UI acknowledgement cannot block that reply. Shutdown stops transport before
closing the pack's runtimes.
