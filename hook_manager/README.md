# Hook manager

## Architectural overview

The hook manager is the trusted in-game side of the launcher. Mod components run
in Wasmtime in the external launcher; this package supplies controlled native
integration through fixed bridge operations and declarative data.

### Transport and bootstrap

`launcher/game_bridge.py` supplies `bridge.js` and the fixed Python adapter
sources to each tracked game session. The transport waits for CPython 3.13 and
enters Python on its main thread with the GIL held. Requests run one at a time
with deadlines, covering status, shutdown, native UI registration, loading
progress, private adapter-definition loading and regulation preparation.

During Python evaluation, the bridge lets other game threads pass its entry
check. The busy guard keeps them from starting another operation. Holding the
script lock while Python releases the GIL could otherwise deadlock a worker.

`bootstrap.py` creates a private manager, readiness, pack discovery, menu, settings
and theme adapters. A source digest and session token identify their owner and prevent
another active controller from replacing them. Later requests reuse the stored
bootstrap function without resending its source. Author Python runs in Wasm.

`diagnostics.py` provides `build_log_record()` with short summaries, complete
exception text, severity and relevant check or action details. Adapters hand them to
`submit_log`; the bridge's `enqueue_log_record` callback holds them for delivery
to the launcher's standard logger. `Manager.store_error()` separately retains
recent error text for status checks. Mod `report` messages continue through their
existing host operation and use INFO severity.

Diagnostic records also carry cached career activity. This plain snapshot can be
read by workers without inspecting game objects; later navigation cannot rewrite
the context of an already-built record.

`adapter_modules.py` handles the private `adapters` operation. It imports only
the launcher's fixed bundle as separate modules, sharing the existing manager
and callable classes. Regulation transport loads only in this private bundle;
bootstrap carries no second copy. It leaves the game's import path alone,
removes its temporary importer after loading and removes owned definitions on
shutdown. Failed imports roll back their definitions. Loading installs no game
hooks and does not declare regulations ready.

The private `prepare-regulations` request supplies a loading-attempt number and an
authenticated local connection. It loads the fixed bundle and publishes a
snapshot of the career's rule data without installing game hooks. Prepared data
and its connection stay owned until cleanup. A failed preparation closes its
connection and reports a compatibility notice without stopping the game.

Fixed installation and activation requests reuse that prepared snapshot. Wrapped
actions are refused temporarily while the launcher checks complete hook coverage;
this does not disable healthy validators. Activation rechecks the career, rule
choices and installed wrappers before enabling them together. Control requests
must match the prepared connection and loading attempt. A stop request restores owned
hooks, waiting for admitted operations to finish before removing them.

### Hooks, discovery and native UI

`manager.py` provides approved target descriptors, bounded plain-value copying,
deferred module observation and owned hook groups. Trusted host code selects
targets and signatures. The manager observes normal imports, installs wrappers
when targets are ready, and restores only its own changes during cleanup.
These facilities are internal; mods are granted the public operations below.

The bridge also observes the game's regulation change manager being imported.
Its shared construction, restore and save hooks retain private automatic-rule
progress with each career, including while all regulation mods are disabled.
This does not run mod code. Prepared automatic policies add weekly wrappers
owned by the regulation change adapter. They run only for the activated career,
stage next-season choices through the game and announce them through its inbox.
Removing the category loader detaches its policies and weekly wrappers while
the change adapter keeps save/load handling installed.
The [regulation adapters](game_adapters/regulations/README.md)
describe the saved data and its checks.

[`callables/`](callables/README.md) adds checks around selected game methods.
Trusted adapters choose the methods and expected arguments, supply copied inputs
and define how a refusal returns to the game. One wrapper runs every registered
check for its method. The game's own refusals stay intact. Failed mod checks
remain unavailable until removed; their wrappers stay installed to prevent
unchecked actions. Only the main thread changes registrations, while no operation
is active. Trusted native wrappers use an `operation()` block to grant a worker
access only while that operation runs. Direct worker calls outside such a block
cannot run required validation.
Existing decorators are checked and called intact. The bootstrap polls pending
work and retries shutdown while operations finish. These registration and native
call facilities are private; no mod permission or bridge request exposes them.

[`game_adapters/`](game_adapters/README.md) separates native contract/project
calculations from regulation selection, proposal formatting and dispatch.
Trusted code owns hook installation; mods cannot load these definitions or
select native targets.

`pack_discovery.py` observes the game's startup Formula selection and retains one
pack snapshot per process. `menu.py` validates mod-owned button declarations and
waits for the normal menu import before adapting native rendering and input.
It also displays loading progress and guards New Season, Load Game and Continue
Season until loading and menu-registration outcomes settle. Ownership and
registry generations keep stale acknowledgements from removing later entries.

`career_preload.py` extends the game's existing loading wait. Continue Season,
Load Game and new-season creation share one preload helper, imported into three
screen modules. Trusted setup wraps those fixed copies, including later imports.
Native asset loading runs normally. Once it completes, the helper reports
unfinished until that loading job's regulation activation succeeds. Input,
drawing and screen transitions remain native. The existing loading message shows
when regulation preparation is pending. Each attempt gets a new setup
number, so a cancelled job's reply cannot release a retry of the same career.
This adapter owns loading approval for the captured screen, career and loading
job. Setup checks those objects without searching the game's stack.
Cleanup restores owned helper copies. For a pack with admitted regulation
declarations, the launcher arms this adapter before enabling career selection.
Changes in loading status reach the controller once; it prepares and activates
the captured attempt before the game completes loading.
`native_readiness.py` owns the shared `ScreenManager.switch_to` observer. Career
loading subscribes to return to the hub or a different career. It revokes approval
locally and reports departure; cancelled
jobs also lose approval. The controller retires the old hooks once admitted work
finishes, clears career rule data and prepares the next captured attempt. Mod
instances, bound validators and their connection stay loaded between careers.

The navigation observer also records menu, save-loading and loaded-career
activity for diagnostics, even with no code mods. The menu adapter supplies the
initial screen. This observation does not hold up loading or wrap its helper.

`settings.py` appends owned Options tabs and manages colour inputs, swatches and
unsaved drafts. It queues save data for the launcher and displays the persistence
acknowledgement. `themes.py` validates palettes and adapts the existing Color Pack
choices, `UITheme`, native colour aliases and chrome lookup. Deferred observation
of Options, colour packs and theme imports applies these adapters when ready.
Selected colours are fixed for the running session; cleanup restores owned
changes, including aliases in modules imported later.

The external loader owns discovery, activation and event validation. Process
replacement creates fresh mod instances and adapters. Closing a session removes
native UI registrations, stops discovery and restores owned wrappers. Loading
status and readiness are trusted infrastructure rather than mod API capabilities.

## Current mod API overview

The implemented public operations are diagnostic reporting, declarative native
menu buttons, native UI settings with mod-local storage, restart-only palettes
and regulation validators. Python source mods use API `0.2.0`, `0.3.0` or `0.4.0`
and the prebuilt runner's
`wit_world` facade. The runner supplies its base class and functions;
authors supply their manifest and Python source without generated bindings.
Mods with prebuilt Wasm components implement the versioned `code-mod` contracts.

| Mod format | API version | Available permissions |
| --- | --- | --- |
| Python source mod | `0.2.0` | `diagnostics`, `main-menu` |
| Python source mod | `0.3.0` | `diagnostics`, `main-menu`, `ui-settings` |
| Python source mod | `0.4.0` | `diagnostics`, `main-menu`, `ui-settings`, `regulations` |
| Mod with prebuilt Wasm component | `0.1.0` | `diagnostics` |
| Mod with prebuilt Wasm component | `0.2.0` | `diagnostics`, `main-menu` |
| Mod with prebuilt Wasm component | `0.3.0` | `diagnostics`, `main-menu`, `ui-settings` |

The manifest selects an exact version and requests unique capabilities. Unknown
versions or permissions and incompatible interfaces fail admission. Python
runners bind every host operation before instantiation: granted operations need
trusted providers, and ungranted operations fail before reaching a provider.
Wasm components may import only operations granted by their manifest. The
[versioned WIT definitions](api/README.md) describe the component contracts.

### Regulation validators

API `0.4.0` source mods can request `regulations`. The pack's root
`regulations.json` holds their `mod_extensions` sections, keyed by manifest ID.
Each section declares categories or native option data and names ordinary
synchronous Python handlers through `validators`. The supported scopes are
`personnel.approach`, `personnel.contract` and `development.project`; trusted adapters select the game
methods and prepare copied proposals.

`personnel.approach` checks driver/staff approaches to Formula teams before
talks start. It supplies person/employer identities, role and intended start,
without salary or duration. The same rule can declare handlers for approach
and contract checks; the mod chooses which inputs each handler needs. Native
screens display approach refusals through their existing feedback.

For both personnel scopes, `context["proposal"]["employer"]` contains `ref`, `name`
and `kind`, plus `prestige_base`, `heritage_prestige`, `form_prestige` and
`nationality` from the loaded employer. Missing values are `null`; prestige values
are finite numbers and nationality is a string. Contract validators can request
`"inputs": ["employer_payroll"]`. The employer then includes `payroll` with
`as_of_week`, `staff_annual_salary_m` and `driver_annual_salary_m`: known annual
base salaries after the proposed deal, in millions. Immediate/backdated deals
use today; future deals use their start week and scheduled contracts. Academy
and supplier contexts provide `null`. No request means no payroll scan.
Participating handlers receive independent copies of the shared input, within
the existing argument limits. Caps and enforcement remain in mod code.
Other employer fields stay private.

The runner calls each handler with fresh `context` and `regulation` dictionaries.
Handlers return allow/deny dictionaries; conditions stay in mod Python. The
selected option supplies parameters for the proposal's effective season. Optional
rookie-length and staff-prestige switches bypass only their named native checks;
other game refusals remain. Declarations belonging to disabled mods are ignored.

The [regulation package](../launcher/regulations/README.md) owns declaration
checks and bounds. Game adapters own native selection and guarded actions; the
launcher owns retained data and execution. This permission supplies no direct
native-call API, endpoint access or new `WitWorld` method. Prebuilt Wasm components
retain their existing API versions and cannot request `regulations`.

### Lifecycle and events

| Public method | Behaviour |
| --- | --- |
| `WitWorld.activate(self)` / WIT `activate: func()` | Runs once after admission and source loading; it may declare initial resources. |
| `WitWorld.on_event(self, name, payload)` / WIT `on-event: func(name: string, payload: string) -> string` | Receives a named event and JSON payload string, and returns a JSON response string after activation. |

Python mods implement `wit_world.WitWorld`. Import-time source code can call
already-granted host operations before activation. The host supplies mod identity
and validates event names, JSON payloads and responses. Values are bounded plain
JSON: null, booleans, finite numbers, signed 64-bit integers, strings, lists and
dictionaries. Nesting is limited to eight levels and 1,024 nodes; each interface
string is limited to 4,096 UTF-8 bytes.

Successful native settings saves deliver `ui-settings.changed` with the complete
saved values document. The launcher supplies no general gameplay event stream or
application of event responses. Menu clicks display their declared confirmation
message and do not invoke `on_event()`.

### Host operations

| Python facade | WIT import | Required permission |
| --- | --- | --- |
| `wit_world.report(message)` | `report: func(message: string)` | `diagnostics` |
| `wit_world.add_menu_button(id, label, message)` | `add-menu-button: func(id: string, label: string, message: string)` | `main-menu`, API `0.2.0`/`0.3.0`/`0.4.0` |
| `wit_world.register_settings(definition)` | `register-settings: func(definition: string) -> string` | `ui-settings`, API `0.3.0`/`0.4.0` |
| `wit_world.register_theme(definition)` | `register-theme: func(definition: string)` | `ui-settings`, API `0.3.0`/`0.4.0` |
| `wit_world.save_settings(values)` | `save-settings: func(values: string) -> string` | `ui-settings`, API `0.3.0`/`0.4.0` |

`report` logs the supplied message with the host-owned mod ID. Non-printable
characters are replaced with spaces before logging.

`add_menu_button` stages a native row owned by the calling mod. Successful
activation publishes declarations to the menu adapter, which inserts rows before
`Exit` when available and uses the game's rendering and input. Clicking a row
displays its confirmation for 2.5 seconds. Declarations contain identifiers and
text; they carry no executable callback or game target.

Item IDs use the same short lowercase identifier format as mod IDs. Labels allow
1-64 printable characters and messages allow 1-160, without surrounding
whitespace. A mod can own four buttons and a process can hold 32. Item IDs are
unique within their owner; labels must also avoid registered and native menu
items. The bridge bounds the encoded declaration list to 16 KiB. Failure or
closure removes that mod's registrations.

### Registration prerequisites

Both Python source mods and mods with prebuilt Wasm components can add an optional
`[prerequisites]` table to `mod.toml`. Each entry holds only the named registration;
the mod still imports and activates normally. Adapter permissions, native imports
and signature checks remain required.

```toml
schema = 2
id = "example.tools"
version = "0.1.0"
api = "0.3.0"
runtime = "python"
entry = "mod"
capabilities = ["main-menu", "ui-settings"]

[prerequisites]
"menu:career_tools" = ["career.loaded", "mod:example.data"]
settings = ["module:ui.screens.calendar_screen"]
```

`menu:<id>` names a button registered by that mod; `settings` names its Options
tab and `theme` its palette. Omitted entries have no extra wait. All conditions
listed for an entry must be met:

- `career.loaded`: a career has reached its game screens. Options overlays retain
  it; returning to the main menu or entering another load removes it.
- `regulations.active`: the current career's regulation integration is active.
  This does not request regulation support or replace its validation checks.
- `mod:<id>`: that enabled code mod has activated and its runner is still available.
- `module:<name>`: the named game module has completed a normal import in this
  process. The launcher never imports it to satisfy the requirement.

Each mod can name eight different conditions, reused across up to six entries.
Conditions do not pause game progress or delay other registrations. Career and
regulation conditions are checked again on departure or replacement; dependent
UI is removed until they are met again. A module condition records its completed
import for the process. Missing conditions leave the affected registration hidden.

Palettes retain their restart-only behaviour: they must be available at startup
to apply. `theme` therefore cannot require `career.loaded` or `regulations.active`.
An added module requirement must be satisfied during startup for a saved palette
choice to apply. Existing declarations and Wasm interfaces are unchanged.

### UI settings and palettes

API `0.3.0` and `0.4.0` grant all three settings/palette operations through `ui-settings`.
Each definition, values document and result is a JSON string limited to 4,096
UTF-8 bytes. `register_settings` declares one Options tab per mod and returns
saved values, or defaults when `settings.json` is absent:

```json
{"schema":1,"label":"Widget Options","fields":[{"id":"accent","label":"Accent Colour","color":"#3070B9"},{"id":"overlay","label":"Overlay","color":"#000000A0"}]}
```

```json
{"schema":1,"values":{"accent":"#3070B9","overlay":"#000000A0"}}
```

Definitions have exactly `schema`, `label` and `fields`. The settings schema is
an integer from 1 to 65,535. There are 1-64 ordered fields, each with exactly
`id`, `label` and a `color` default. IDs contain 1-64 ASCII characters, beginning
with a lowercase letter and continuing with lowercase letters, digits, `.`, `_`,
`:` or `-`; they are unique within the mod. Labels contain 1-64 printable
characters without surrounding whitespace. Defaults use `#RRGGBB` or `#RRGGBBAA`.
Only colour fields are implemented. Equal colours remain separate field IDs.

At most four mod tabs register per process, with distinct titles that also avoid
built-in tabs. Registration creates no file. An identical definition can be
registered again to read current values; changing it during the session fails.
Invalid, oversized or schema-incompatible saved settings fail registration
without overwriting the file. Schema migrations are not automatic.

`save_settings` accepts and returns the complete `schema`/`values` document.
Every registered field must appear exactly once; additional fields are rejected.
Colours accept either letter case and normalise to uppercase, retaining each
field's RGB or RGBA width. Saving the declared defaults implements reset.
The launcher owns the fixed mod-local `settings.json` path, rejects linked,
shared or special files, and saves by atomic replacement. Failed writes preserve
the previous file and in-memory values.

The native editor holds unsaved drafts across tab switches. Save requests cross
the bridge as bounded data with owner, request ID and registry generation.
The launcher persists and acknowledges them before delivering
`on_event("ui-settings.changed", payload)` with the complete saved values.
Failed writes preserve the draft and show a retry message. Notification failure
closes the affected mod and removes its registrations; saved values remain.

`register_theme` independently stages one named palette per mod, up to four per
process. Its JSON object contains exactly `label` and `colours`. Labels follow
the settings label rules and must avoid registered and native choices. `colours`
contains every role in [`THEME_ROLES`](themes.py): 59 named entries with
`"#RRGGBBAA"` strings for `OVERLAY` and `"#RRGGBB"` strings for every other role.
Roles sharing a native
default colour must retain the same selected colour; the native adapter checks
this coupling when applying the palette. The [theme example](../examples/theme_mod/README.md)
maps these roles to its own editable groups.

Re-registration replaces the mod's staged palette. The host assigns the Color
Pack ID `code-mod:<mod-id>`; palette registration does not write settings.
The native menu owns choice persistence. Choices and colour edits apply after
restart, and a missing provider falls back to the game's default. Palette
application covers `UITheme`, imported native aliases and the chrome colour
lookup. Already-resolved colours bypass further translation. Artwork, arbitrary
drawing literals and computed colours are outside this named-role contract.

Closure removes owned tabs, palettes and wrappers while keeping saved settings
and the native theme choice. The [0.3.0 contract architecture](api/0.3.0/README.md)
describes how these operations cross the guest, host and native boundaries.

### Execution boundary

Python source mods in one pack share a Python interpreter and Wasm Store. They
can share Python objects; permissions and resources are still attributed to
individual mods. A shared runner failure closes the whole source group. Mods
with prebuilt Wasm components have separate Stores. Each Store has a 64 MiB
guest linear-memory limit. The trusted Python runner's source-transfer
operations are private loading support, not public mod capabilities.

| Execution phase | Fuel | Deadline |
| --- | ---: | ---: |
| Shared Python interpreter initialisation | 20,000,000 | 5 seconds |
| Python source transfer and import, shared across loading calls | 256,000,000 | 5 seconds |
| Wasm component initialisation | 5,000,000 | 1 second |
| Activation or one event | 5,000,000 | 1 second |

Each phase or ordinary call permits 64 host calls. Fuel counts Wasm execution
work, including the guest Python interpreter. Deadline checks include host work
and post-return cleanup. Trusted handlers are not preempted; returns after the
deadline are rejected.
Guest calls run in order. Traps and limit failures close the affected instance
and release its owned resources. Components receive no host WASI filesystem,
network or process environment.
