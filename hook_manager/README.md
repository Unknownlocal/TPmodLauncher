# Hook manager

## Architectural overview

The hook manager is the trusted in-game side of the launcher. Mod components run
in Wasmtime in the external launcher; this package supplies controlled native
integration through fixed bridge operations and declarative data.

### Transport and bootstrap

`launcher/game_bridge.py` supplies `bridge.js` and the fixed Python adapter
sources to each tracked game session. The transport waits for CPython 3.13 and
enters Python on its main thread with the GIL held. Requests are serialized and
bounded, covering status, shutdown, menu/settings registration and loading progress.

`bootstrap.py` creates a private manager, pack discovery and menu adapter. A
source digest and session token identify their owner and prevent another active
controller from replacing them. The bridge loads trusted adapter source; it does
not import author Python into the game.

### Hooks, discovery and menu adaptation

`manager.py` provides approved target descriptors, bounded plain-value copying,
deferred module observation and owned hook groups. Trusted host code selects
targets and signatures. The manager observes normal imports, installs wrappers
when targets are ready, and restores only its own changes during cleanup.
These facilities are internal; mods are granted the public operations below.

`pack_discovery.py` observes the game's startup Formula selection and retains one
pack snapshot per process. `menu.py` validates mod-owned button declarations and
waits for the normal menu import before adapting native rendering and input.
It also displays loading progress and guards New Season, Load Game and Continue
Season until loading and menu-registration outcomes settle. Ownership and
registry generations keep stale acknowledgements from removing later entries.

The external loader owns discovery, activation and event validation. Process
replacement creates fresh mod instances and adapters. Closing a session removes
menu registrations, stops discovery and restores owned wrappers. Loading status
and readiness are trusted infrastructure rather than mod API capabilities.

## Current mod API overview

The implemented public operations are diagnostic reporting, declarative native
menu buttons and native UI settings with mod-local storage. Python source mods
use schema 2, API `0.2.0` or `0.3.0` and the prebuilt
runner's `wit_world` facade. The runner supplies its base class and functions;
authors supply their manifest and Python source without generated bindings.
Explicit schema 1 Wasm components retain the versioned `code-mod` contracts.

| Mod format | API version | Available permissions |
| --- | --- | --- |
| Schema 2 Python source | `0.2.0` | `diagnostics`, `main-menu` |
| Schema 2 Python source | `0.3.0` | `diagnostics`, `main-menu`, `ui-settings` |
| Schema 1 Wasm component | `0.1.0` | `diagnostics` |
| Schema 1 Wasm component | `0.2.0` | `diagnostics`, `main-menu` |
| Schema 1 Wasm component | `0.3.0` | `diagnostics`, `main-menu`, `ui-settings` |

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
| `wit_world.add_menu_button(id, label, message)` | `add-menu-button: func(id: string, label: string, message: string)` | `main-menu`, API `0.2.0` |
| `wit_world.register_settings(definition)` | `register-settings: func(definition: string) -> string` | `ui-settings`, API `0.3.0` |
| `wit_world.register_theme(definition)` | `register-theme: func(definition: string)` | `ui-theme`, API `0.3.0` |
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

UI settings are validated and stored by the external launcher. `settings.py`
owns appended native Options tabs, hex input, swatches, draft state and save
requests. The bridge checks for native settings changes at most four times per
second while tabs are registered; idle UI does not call sandboxed mods. Save
requests are acknowledged after persistence and then notify the owning mod. The
[UI settings contract](api/0.3.0/README.md) defines fields, limits and storage
ownership.

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
