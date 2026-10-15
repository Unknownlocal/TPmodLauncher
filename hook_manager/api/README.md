# Hook manager and mod API

## Architectural overview

The hook manager is the trusted in-game side of the launcher. Mod components
execute in the external launcher process; the manager supplies controlled access
to native game interfaces through fixed bridge operations and declarative data.

`launcher/game_bridge.py` assembles the trusted manager sources and loads
`hook_manager/bridge.js` into each tracked game session. The JavaScript transport
observes the CPython 3.13 runtime and enters Python at a main-thread boundary
with the GIL held. Requests are serialized, bounded and restricted to status,
shutdown, menu registration and loading progress.

`hook_manager/bootstrap.py` creates a private manager instance with a source
digest and session ownership token. It initializes the hook manager, pack
discovery and menu adapter, then dispatches bridge requests to those instances.
Ownership checks prevent an unrelated manager from being overwritten.

`hook_manager/manager.py` provides approved target descriptors, bounded plain-value
validation, deferred module observation and owned hook registration. It checks
target signatures and restores wrappers during cleanup. These are trusted host
facilities; the public mod contract exposes only the granted operations listed
below.

`hook_manager/pack_discovery.py` observes the game's startup selection and keeps
one Formula-pack snapshot per process. `hook_manager/menu.py` validates owned
button declarations, waits for the normal menu import and adapts the game's
native menu. The adapter also displays loader progress and guards season
actions until loading and registration outcomes settle. Registry generation
counters prevent stale acknowledgements from revoking later registrations.

Outside the game, `launcher/pack_mods.py` owns discovery and activation;
`launcher/mod_contract.py` validates manifests and binds permissions to trusted
handlers. `launcher/mod_api.py` defines supported versions and lifecycle
signatures, while the versioned `mod.wit` files define the component interface.
`launcher/build_mod.py` generates author bindings from that contract.
`launcher/sandbox.py` enforces admission and execution limits for each instance.
Process replacement and shutdown close the owned mod scopes and bridge, remove
menu registrations and restore the manager's hooks.

## Mod API overview

The mod API is the versioned `code-mod` WebAssembly Component contract. Mods run
in Wasmtime in the external launcher process and call explicitly granted host
functions through generated bindings. The implemented capabilities are
diagnostic reporting and declarative native-menu buttons.

### Supported versions and permissions

| Manifest API version | Contract | Available permissions |
| --- | --- | --- |
| `0.1.0` | [mod.wit](0.1.0/mod.wit) | `diagnostics` |
| `0.2.0` | [mod.wit](0.2.0/mod.wit) | `diagnostics`, `main-menu` |

The manifest selects an exact API version and requests its permissions through
`capabilities`. Unknown versions, unknown or duplicate permissions, ungranted
imports and incompatible public signatures fail admission. API 0.2.0 uses WIT
feature annotations to select imports when generating bindings; runtime checks
separately enforce the permissions. There is no automatic version fallback.

### Lifecycle exports

Both versions require these exports from the mod component:

| WIT signature | Python implementation | Behaviour |
| --- | --- | --- |
| `activate: func()` | `WitWorld.activate(self)` | Called once after component admission and initialization. Registers the mod's initial host resources. |
| `on-event: func(name: string, payload: string) -> string` | `WitWorld.on_event(self, name, payload)` | Accepts an event name and JSON payload string and returns a JSON response string. |

Python components implement `wit_world.WitWorld` using the bindings generated
from the selected contract. Component initialization can call granted host
functions before `activate()` runs. Event calls require successful activation.

The host implements event serialization and response validation, but the current
launcher does not deliver general gameplay events or apply event responses to
gameplay. Menu clicks display their registered confirmation; they do not call
`on_event()`.

### Host functions

| WIT signature | Python binding | Required permission |
| --- | --- | --- |
| `report: func(message: string)` | `wit_world.report(message)` | `diagnostics` |
| `add-menu-button: func(id: string, label: string, message: string)` | `wit_world.add_menu_button(id, label, message)` | `main-menu`, API `0.2.0` |

`report` writes a diagnostic message through the launcher. The host supplies the
calling mod's identity and replaces non-printable characters with spaces before
logging the message.

`add-menu-button` stages a native menu row owned by the calling mod. After
successful activation, the launcher publishes the declarations to the trusted
menu adapter. The adapter waits for the game's normal menu import, inserts rows
before `Exit` when present, and uses the native rendering and input handling.
Clicking a row displays its confirmation message for 2.5 seconds.

Menu declarations have these limits:

- Item IDs start with a lowercase letter and contain up to 128 lowercase letters,
  digits, `.`, `_`, `:` or `-` characters.
- Labels contain 1-64 printable characters; messages contain 1-160. Leading and
  trailing whitespace are rejected.
- Each mod can own four buttons, with 32 total per game process. Item IDs must be
  unique within their owning mod, and labels must be unique across mod buttons
  and existing native menu entries.
- The complete declaration sent through the bridge is limited to 16 KiB of
  encoded JSON.

The host supplies ownership. Declarations contain text and identifiers; they
cannot supply executable callbacks, game targets or commands. A failed or closed
mod loses its owned registrations. Session shutdown removes the registry and
restores owned menu wrappers.

### Values and execution limits

Each interface string is limited to 4,096 UTF-8 bytes. Event payloads and
responses contain plain JSON values: null, booleans, strings, finite numbers,
signed 64-bit integers, lists and dictionaries. Validation limits nesting depth
to 8 and the value tree to 1,024 nodes. Invalid event data or responses close the
affected mod. Native objects, pointers and callable targets cannot cross the
interface.

Each mod has a separate Store, grants and guest state. The host limits component
files to 32 MiB, guest linear memory to 64 MiB and host calls to 64 per
initialization or exported call.

| Runtime profile | Initialization budget | Budget per exported call |
| --- | --- | --- |
| `python` | 20,000,000 fuel units, 5 seconds | 5,000,000 fuel units, 1 second |
| `wasm` | 5,000,000 fuel units, 1 second | 5,000,000 fuel units, 1 second |

Fuel measures Wasm execution work, including the guest Python interpreter.
Budgets reset for each call and include post-return cleanup. Trusted host
handlers do not consume guest fuel; host-call counts and elapsed-time checks
bound that work separately. A deadline can interrupt guest execution, but cannot
interrupt a blocked host handler. A late return is rejected.

Guest calls are serialized. Traps and execution-limit failures close the
affected instance, revoke its grants and release its owned resources. Guest
components receive only the linked API operations, with no host WASI filesystem,
network or process environment.

### Loader integration

The loader activates admitted mods from the selected Formula pack in mod-ID
order and maintains their lifetime with the tracked game process. Replacement
processes receive fresh instances. Shutdown closes mods in reverse activation
order.

Loading progress and season-action readiness belong to the loader and native
menu adapter. They use the trusted bridge to keep New Season, Load Game and
Continue Season unavailable until loading and menu-registration outcomes settle.
They are not mod API capabilities.
