# Regulation definitions and validation

This private package reads enabled mods' regulation definitions and prepares their
Python validation functions to run inside Wasm. The launcher manages loading,
rule data and execution. Trusted game adapters supply the places where checks
run. Mod code cannot select a game function or call the private callable engine.
Source API `0.4.0` admits the `regulations` permission. Earlier source APIs keep
their existing operations; the runner interface and native callable engine remain
private.

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

`custom_categories` and `custom_scripts` copy enabled mods' declarations into the
fixed loading request after handlers bind. No file is rewritten. The
[native integration](../../hook_manager/game_adapters/regulations/README.md) owns
loading hooks, checks definitions before publication and preserves the loaded
policy mode and saved script progress. Matching scripts are reused; conflicts
prevent activation. The request permits 128 categories and 4,096 script rows
within 2 MiB, including owner-prefixed IDs. The file limit remains 1 MiB.

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

Checked native switches form part of rule revisions. After career publication,
the runtime supplies checked booleans to the fixed installation request. Each switch must
report `native` for true or `disabled` for false before activation; incompatible
switches prevent readiness. Failed setup restores installed hooks. Switch-only
mods publish empty rule data and need no handlers. Required-owner failure closes
transport and restores bypassed native checks while ordinary action guards
continue refusing unchecked actions. The native integration owns these hooks.

Driver contexts include `proposal.native_checks.rookie_contract_length_applies`.
The original game helper calculates it for the proposed person, role and employer
even when the native cap is disabled. Validators can combine it with adopted rule
data. The [driver adapter](../../hook_manager/game_adapters/drivers/README.md)
explains how preparation avoids changing live game data.

Limits are 32 owners, 32 categories/validators per owner, 32 options per custom
category, 128 categories/256 validators per session and 128 scripts per owner.
Each validator's complete context and rule together must fit 4,096 UTF-8 bytes,
1,024 nodes and eight container levels. These are invocation limits, separate
from the 1 MiB file limit. Failed preparation returns no partial owner set.

## Loading checks and keeping rule data in memory

`PackMods.bind_regulations()` creates a `RegulationRuntime`. It binds handler
tables once in the pack's shared source runner through `SharedHandlerRunner`.
It rejects owners using separate runners. Mods keep their own IDs and declarations,
with one career number shared by all installed data. Owners declaring data without
validators stay required but need no handler table. A single source mod uses the
same binding path.

Handlers are synchronous two-argument functions from the accepted owner source.
Functions may be re-exported within the mod's checked source; mod Python is never
imported into the launcher.
Binding uses the source-import allowance: 256 million fuel units and five seconds
per owner. Calls return `{"decision":"allow"}` or
`{"decision":"deny","message":"Reason"}`. Refusals need 1-160 trimmed printable
characters. Conditions stay in mod code.

The [validation package](validation/README.md) owns the combined catalogue,
career installation and reference-based dispatch. Rule data stays in memory between actions.
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

`RegulationRuntime` owns the captured career number and its preparation,
installation, activation and retirement. The bridge sends fixed requests and
services regulation transport while awaiting a reply. `PackMods` derives its UI
holds and failure display from runtime status, refreshing between completed
requests so cleanup cannot start another request through a busy bridge.

Normal startup binds admitted handlers once and confirms that `CareerPreload`
protects career selection. Each captured attempt publishes its rule data before
hooks install. Coverage is checked with validation held off; readiness and mod
UI are released only after the game confirms activation. Confirmed coverage
already permits validation if native guards enable just before that reply; the
game controls when guarded actions begin. A failed setup closes transport
before requesting native restoration. An invalid acknowledgement cannot
leave an earlier approval active.

Repeated or older waiting notices do not repeat setup. Departure numbers remain
recorded so a cancelled attempt cannot restart from a delayed notice. Retirement
keeps the connection and handlers, but the next career must publish fresh data
and confirm coverage again. Native captured-job and ownership checks remain in
the game process. No enabled declarations means no native loading hooks.
Setup failures are reported through command-line output and the log. A failed
validator's native bypasses are restored while its action guards stay unavailable.

The [validation package](validation/README.md) owns complete career publication
and reset. Private option data stays in the launcher; native choices arrive in
bounded pages under one deadline, with complete acknowledgement before use.
Partial updates and repeat installation are rejected. Reset retains bindings but
requires fresh data and hook approval; it cannot revive a failed runner.

Each action carries one shared proposal, rule selections and owner binding-slot
references. Each declared validator runs once, up to 256 per action. Requests and
replies each use one JSON frame, bounded to 2 MiB. This allows long season integers
within the existing argument limits. Policy data still travels in bounded pages;
partial socket reads and writes keep the original deadline.
An identified group failure can keep the connection open; broken transport makes
every required owner unavailable. There is no reconnection or stale approval reuse.
The server has a private wakeup connection so shutdown can interrupt an idle read
even while the native client remains connected; it needs no idle polling loop.

`PackMods` reserves required owners before imports or activation; cleanup and
failure never remove that requirement. Store failure includes definition-only
owners, while wire failure replies name only advertised execution owners.
Readiness requires committed policy and trusted coverage of each required scope:
driver, staff, academy and staffing for `personnel.contract`, or projects for
`development.project`. Missing coverage blocks readiness; unrelated coverage does
not. Unknown scopes are rejected before binding. Mods cannot approve coverage.

UI stays staged while rules are unready and is cleared on failure. Cleanup removes
all failed peers' declarations before publishing replacements. Native UI updates
wait until validation replies have been released, so UI acknowledgement cannot
block a decision. Shutdown stops transport before closing runtimes.
