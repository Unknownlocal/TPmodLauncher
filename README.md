# modLauncher

## Licence and attribution

- [Licence](LICENCE)
- [Attribution](ATTRIBUTION.txt)

## Run the launcher

Keep `modLauncher` directly beside `TeamPrincipal.exe` in the game's installation
folder. Use the prepared launcher bundle, including its `libs` directory and
`bundled_packages.toml`. The launcher uses its bundled Windows x64 Python runtime
and dependencies.

In Steam, open the game's **Properties > General > Launch Options** and enter:

```text
"<game-root>\modLauncher\libs\python-3.13.16-embed-amd64\python.exe" -B "<game-root>\modLauncher\launcher\entry.py" -- %command%
```

Replace both occurrences of `<game-root>` with the full installation path of
Team Principal: A Racing Manager. Keep the quotes, `--` and `%command%` as shown,
then start the game through Steam. Steam supplies the installed executable and
its arguments after `--`.

Select the Formula data pack containing your installed code mods. The launcher
loads that process's startup pack and stays running while it tracks the game and
replacement processes. Restart the game to load changed mods or a different pack.

Loading results and mod diagnostics appear in `modLauncher/logs/launcher.log`,
which is replaced at each launcher run. The native menu shows loading progress
and keeps season actions unavailable until code loading and menu registration
have settled.

## Instructions for modders

### Create a mod

Start with the [diagnostic example](examples/diagnostic_mod) or
[menu example](examples/menu_mod). Each mod contains a `mod.toml` manifest,
readable Python source in `src/mod.py`, and a compiled `mod.wasm` component.

```toml
schema = 1
id = "my.mod"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
component = "mod.wasm"
capabilities = ["diagnostics", "main-menu"]
```

All seven fields are required; additional fields are rejected.

| Field | Accepted value |
| --- | --- |
| `schema` | Integer `1`. |
| `id` | Unique ID, at most 128 characters, starting with a lowercase letter and continuing with lowercase letters, digits, `.`, `_`, `:` or `-`. |
| `version` | SemVer version, at most 64 characters. |
| `api` | Exact contract version: `0.1.0` or `0.2.0`. |
| `runtime` | `python` for components built with the supplied Python builder; `wasm` for the standard Wasm resource profile. |
| `component` | Relative `.wasm` path inside this mod's folder, with no links or path traversal. |
| `capabilities` | Unique permissions: `diagnostics`, and optionally `main-menu` with API `0.2.0`. An empty list is valid. |

Implement the supplied contract through the generated `wit_world` bindings:

```python
import wit_world


class WitWorld(wit_world.WitWorld):
    def activate(self):
        wit_world.report("My mod activated")
        wit_world.add_menu_button(
            "confirmation", "My Mod", "My mod is active"
        )

    def on_event(self, name, payload):
        return "null"
```

`activate()` runs once after admission. `report()` requires `diagnostics`;
`add_menu_button()` requires API `0.2.0` and `main-menu`. Request only the
permissions used by your mod. The builder selects the supplied WIT contract and
generates bindings from the manifest; authors implement those bindings.

Menu registrations supply an item ID, label and confirmation message. Item IDs
follow the manifest ID rules. Labels contain 1-64 printable characters and
messages contain 1-160, without leading or trailing whitespace. Each mod can
register four buttons, with 32 total per game process. IDs must be unique within
the mod, and labels must be unique across registered and existing native items.
Clicking a button displays its confirmation for 2.5 seconds through the game's
native menu.

`on_event(name, payload)` accepts and returns JSON strings. The host validates
plain JSON values, with a depth limit of 8, a 1,024-node budget and a 4,096-byte
UTF-8 limit per interface string. General gameplay event delivery is not
implemented; menu clicks display their declared confirmation message.

### Build the component

Use a full developer Python 3.13 installation. In PowerShell, from the
`modLauncher` folder, create a development environment and install the pinned
tools:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Build your mod using that environment:

```powershell
& .\.venv\Scripts\python.exe -m launcher.build_mod "C:\path\to\my_mod" --output "C:\path\to\my_mod\mod.wasm"
```

The builder uses `componentize-py==0.25.1`, `src/mod.py` and the selected API
contract. It accepts up to 1 MiB of Python source, rejects linked and non-Python
inputs, and builds in temporary staging with WASI denial stubs. Choose a fresh
output path when rebuilding: existing components are preserved. If the output
filename changes, update the manifest's `component` field to match.

### Install and distribute

The mod author supplies all three files:

| File | Responsibility |
| --- | --- |
| `src/mod.py` | Written by the mod author. |
| `mod.toml` | Written by the mod author, using the manifest format above. The build tool reads this file; it does not generate it. |
| `mod.wasm` | Built by the mod author from their source using our supplied `launcher.build_mod` tool, then included in the distributed mod. |

We supply the API contract and build tool. Launcher maintainers do not build each
author's mod, and the player launcher does not generate the manifest or compile
Python source at startup.

The mod author distributes the manifest, compiled component and readable source
together. Players install that complete mod folder as a direct child of the
selected Formula data pack:

```text
<game-root>/
  TeamPrincipal.exe
  modLauncher/
  data/<selected-data-pack>/
    my_mod/
      mod.toml
      mod.wasm
      src/mod.py
```

Several code mods can share a data pack. Valid mods activate in ascending mod-ID
order; every candidate sharing a duplicate ID is rejected. Discovery accepts up
to 4,096 direct pack entries and 32 candidate mod folders. Linked folders and
files are rejected or skipped. Manifests are limited to 16 KiB and components to
32 MiB. Players receive compiled components; launcher startup uses those
components without a source build step.

Check the launcher log for `CODE MOD ACTIVE`, `CODE MOD REPORT` and
`CODE MOD MENU` records. Rejections identify the affected mod. Each process gets
fresh mod state, and shutdown or process replacement releases its mod instances.

Mods run with a 64 MiB guest linear-memory limit, up to 64 host calls per
initialization or exported call, and fresh execution budgets. The `python`
profile allows 20,000,000 fuel units and five seconds for initialization; later
calls allow 5,000,000 units and one second. The `wasm` profile also uses the
smaller budget for initialization. Fuel measures Wasm execution work, including
the guest Python interpreter. Exceeding a budget or trapping closes the affected
mod and removes its owned registrations. The host links only granted API
functions; guest components receive no host WASI filesystem, network or process
environment.

## Architectural overview

### Launch and process ownership

`launcher/entry.py` verifies the bundled interpreter and dependency inventory,
loads the trusted launcher packages and validates Steam's command against the
installed `TeamPrincipal.exe`. `process_lifecycle.py` holds a controller lock,
starts the game through Frida and tracks child processes, exits, replacement,
exec transitions and PID reuse. Each tracked game session owns its bridge and
mod instances.

### Game bridge and native adapters

`launcher/game_bridge.py` loads the trusted `hook_manager/bridge.js` transport.
The transport enters CPython 3.13 at a main-thread boundary with the GIL held;
`bootstrap.py` creates an owned manager from fixed launcher sources.
`manager.py` provides deferred module observation, hook ownership and restoration.
`pack_discovery.py` reads the game's selected startup Formula pack and retains
one snapshot per process.

The bridge carries bounded status, shutdown, menu and loading requests.
`hook_manager/menu.py` installs declarative menu rows using the game's renderer
and input handling, publishes loading status and guards season actions during
startup. Registration ownership and generation counters support cleanup and
reject stale acknowledgements.

### Discovery, compilation and execution

`launcher/pack_mods.py` scans the selected pack's direct children, validates all
manifests, rejects duplicate IDs and loads the remaining mods independently.
`mod_contract.py` binds manifest permissions to trusted host handlers;
`mod_api.py` and the versioned WIT files define accepted imports and lifecycle
signatures.

`compiled_mod.py` validates and compiles portable components on a background
worker. `compilation_cache.py` configures Wasmtime's persistent cache under
`bin/wasmtime-cache`, checking local ownership and access before use and falling
back to compilation without caching when those checks fail. Every load still
validates current inputs and creates fresh grants, a Store and guest state.

`sandbox.py` owns each component's runtime resources, enforces signatures and
execution limits, and serializes guest calls on the controller thread. Mod
components execute in Wasmtime in the external launcher process. Menu
declarations cross to the trusted native adapter after activation. Session
closure releases mods in reverse activation order and restores owned hooks.
`load_timing.py` records discovery and per-mod loading phases in the launcher log.

### Build and packaging

`mod_runtime` defines the private Python-runner ABI and a separate developer
builder/verifier for a single prebuilt runner. Its typed host-operation registry
supports per-mod grants through `launcher/runner_host.py`, which binds trusted
providers or rejection callbacks before sandbox instantiation. Bundles retain their source,
upstream notice texts and a bounded integrity inventory. This artifact foundation
is separate from the current player loading path.
`mod_runtime/source.py` validates source manifests and snapshots bounded Python
source as inert text, using pinned directory handles and ordinary-file checks.
`mod_runtime/runtime.py` transfers snapshots into the trusted runner, shares
bounded fuel and time across source initialization, and owns lifecycle cleanup.
Activation and event calls retain the sandbox's ordinary per-call limits.
`launcher/source_mod.py` adapts this runner to the existing mod lifecycle, JSON
event validation and host-owned diagnostic/menu identity.
`mod_runtime/preparation.py` snapshots source and compiles verified runner bytes
on a caller-owned worker, with controller adoption and cancellation cleanup.

`build_mod.py` builds author components separately from player startup.
`build_frida.py` produces the reduced native transport; `frida_sources.py`
exports and restores matching build sources. `prepare_release.py` assembles
runtime dependencies and records their file inventory in `bundled_packages.toml`.
The test tree separates launcher and hook-manager behaviour, with owned fixtures
for component execution, native bridge sessions and packaged-runtime probes.
