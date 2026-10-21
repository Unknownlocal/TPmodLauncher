# Hook manager

## Architectural overview

The hook manager is the trusted in-game side of the launcher. Mod components run
in Wasmtime in the external launcher; this package supplies controlled native
integration through fixed bridge operations and declarative data.

### Transport and bootstrap

`launcher/game_bridge.py` supplies `bridge.js` and the fixed Python adapter
sources to each tracked game session. The transport waits for CPython 3.13 and
enters Python on its main thread with the GIL held. Requests are serialized and
bounded, covering status, shutdown, native UI registration and loading progress.

`bootstrap.py` creates a private manager, pack discovery, menu, settings and theme
adapters. A source digest and session token identify their owner and prevent
another active controller from replacing them. The bridge loads trusted adapter source; it does
not import author Python into the game.

### Hooks, discovery and native UI

`manager.py` provides approved target descriptors, bounded plain-value copying,
deferred module observation and owned hook groups. Trusted host code selects
targets and signatures. The manager observes normal imports, installs wrappers
when targets are ready, and restores only its own changes during cleanup.
These facilities are internal; mods are granted the public operations below.

[`callables/`](callables/README.md) adds a private synchronous guard layer using
the same import watcher and restoration journal. Trusted adapters approve native paths and call signatures,
provide read-only projections and structural denial contracts, and register owned
bindings. One wrapper multiplexes all owners of a target. Native refusals survive
query extensions; identified validator failures latch only that owner unavailable
and retain guards until explicit removal. Main-thread binding changes are rejected
during active operations; polling defers pending installation until idle. Trusted
native workers require enrollment and an operation scope.
Decorator chains are verified and called intact. The bootstrap owns polling and
cleanup, retrying shutdown through the bounded bridge request while operations
finish; no bridge request, manifest capability or guest operation
exposes registration or native calls.

[`game_adapters/`](game_adapters/README.md) separates native driver/project
knowledge from regulation binding, context assembly and dispatch. These are
private sibling integrations above the callable mechanism; moving them adds no
mod imports, native targets or public capability.

`pack_discovery.py` observes the game's startup Formula selection and retains one
pack snapshot per process. `menu.py` validates mod-owned button declarations and
waits for the normal menu import before adapting native rendering and input.
It also displays loading progress and guards New Season, Load Game and Continue
Season until loading and menu-registration outcomes settle. Ownership and
registry generations keep stale acknowledgements from removing later entries.

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
menu buttons, native UI settings with mod-local storage and restart-only palettes.
Python source mods use API `0.2.0` or `0.3.0` and the prebuilt runner's
`wit_world` facade. The runner supplies its base class and functions;
authors supply their manifest and Python source without generated bindings.
Mods with prebuilt Wasm components implement the versioned `code-mod` contracts.

| Mod format | API version | Available permissions |
| --- | --- | --- |
| Python source mod | `0.2.0` | `diagnostics`, `main-menu` |
| Python source mod | `0.3.0` | `diagnostics`, `main-menu`, `ui-settings` |
| Mod with prebuilt Wasm component | `0.1.0` | `diagnostics` |
| Mod with prebuilt Wasm component | `0.2.0` | `diagnostics`, `main-menu` |
| Mod with prebuilt Wasm component | `0.3.0` | `diagnostics`, `main-menu`, `ui-settings` |

The manifest selects an exact version and requests unique capabilities. Unknown
versions or permissions and incompatible interfaces fail admission. Python
runners bind every host operation before instantiation: granted operations need
trusted providers, and ungranted operations fail before reaching a provider.
Wasm components may import only operations granted by their manifest. The
[versioned WIT definitions](api/README.md) describe the component contracts.

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
| `wit_world.add_menu_button(id, label, message)` | `add-menu-button: func(id: string, label: string, message: string)` | `main-menu`, API `0.2.0` or `0.3.0` |
| `wit_world.register_settings(definition)` | `register-settings: func(definition: string) -> string` | `ui-settings`, API `0.3.0` |
| `wit_world.register_theme(definition)` | `register-theme: func(definition: string)` | `ui-settings`, API `0.3.0` |
| `wit_world.save_settings(values)` | `save-settings: func(values: string) -> string` | `ui-settings`, API `0.3.0` |

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

### UI settings and palettes

API `0.3.0` grants all three settings/palette operations through `ui-settings`.
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
Colours accept either letter case and normalize to uppercase, retaining each
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

Each mod has its own Store, permissions and guest state, with a 64 MiB guest
linear-memory limit. The trusted Python runner has a private loading protocol;
its source-transfer operations are not public mod capabilities.

| Execution phase | Fuel | Deadline |
| --- | ---: | ---: |
| Python interpreter initialization | 20,000,000 | 5 seconds |
| Python source transfer and import, shared across loading calls | 256,000,000 | 5 seconds |
| Wasm component initialization | 5,000,000 | 1 second |
| Activation or one event | 5,000,000 | 1 second |

Each phase or ordinary call permits 64 host calls. Fuel counts Wasm execution
work, including the guest Python interpreter. Deadline checks include host work
and post-return cleanup. Trusted handlers are not preempted; returns after the
deadline are rejected.
Guest entry is serialized. Traps and limit failures close the affected instance
and release its owned resources. Components receive no host WASI filesystem,
network or process environment.
