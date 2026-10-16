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
[menu example](examples/menu_mod). Write a `mod.toml` manifest and readable Python
source in `src/mod.py`:

```toml
schema = 2
id = "my.mod"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics", "main-menu"]
```

All seven fields are required; additional fields are rejected.

| Field | Accepted value |
| --- | --- |
| `schema` | Integer `2`. |
| `id` | Unique ID, at most 128 characters, starting with a lowercase letter and continuing with lowercase letters, digits, `.`, `_`, `:` or `-`. |
| `version` | SemVer version, at most 64 characters. |
| `api` | Exact source API version `0.2.0`. |
| `runtime` | `python`. |
| `entry` | Dotted module name under `src/`, at most 128 characters; `mod` loads `src/mod.py`. |
| `capabilities` | Unique permissions: `diagnostics` and `main-menu`. An empty list is valid. |

Implement the `wit_world` API provided by the runner:

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
`add_menu_button()` requires `main-menu`. Request only the permissions used by
your mod. The runner supplies `wit_world.WitWorld`, `report()` and
`add_menu_button()`; mods do not generate or distribute API bindings.

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

### Install and distribute

The mod author supplies the manifest and source. Distribute them together as a
mod folder, installed directly inside the selected Formula data pack:

```text
<game-root>/
  TeamPrincipal.exe
  modLauncher/
  data/<selected-data-pack>/
    my_mod/
      mod.toml
      src/mod.py
```

The launcher loads your Python source into its bundled trusted Wasm runner at
startup. Authors and players need no component build tool, generated bindings or
`mod.wasm`. The launcher reads the supplied manifest; it does not generate it.

You can include additional Python modules and packages under `src/`. Packages
require `__init__.py`; module names must be portable ASCII Python identifiers.
Paths must be unambiguous across case-sensitive and case-insensitive systems.
Top-level module names must not shadow the runner API or Python standard library.
The source snapshot accepts only UTF-8 `.py` files: up to 1 MiB across 256 files,
512 tree entries, 16 path levels and 256 characters per relative path. Third-party
packages and native extensions are not bundled. The frozen guest standard-library
profile includes `json`, `math`, `os` and their runtime dependencies; availability
of a module does not provide access to host files, networking or processes.

Several code mods can share a data pack. Valid mods activate in ascending mod-ID
order; every candidate sharing a duplicate ID is rejected. Discovery accepts up
to 4,096 direct pack entries and 32 candidate mod folders. Linked folders and
files are rejected or skipped. Manifests are limited to 16 KiB. A source manifest
or source-loading failure rejects that mod without falling back to a `mod.wasm`.

Explicit schema 1 `runtime = "wasm"` components remain supported with a relative
`component` path and a 32 MiB component limit. Schema 1 Python components are not
accepted by the source-enabled launcher.

Check the launcher log for `CODE MOD ACTIVE`, `CODE MOD REPORT` and
`CODE MOD MENU` records. Rejections identify the affected mod. Each process gets
fresh mod state, and shutdown or process replacement releases its mod instances.

Each mod has separate guest state and a 64 MiB guest linear-memory limit.
Python interpreter initialization allows 20,000,000 fuel units and five seconds.
Source transfer and import then share 256,000,000 fuel units and five seconds;
activation and each event allow 5,000,000 units and one second. Each phase allows
up to 64 host calls. Standard Wasm components use the smaller execution budget
for initialization as well. Fuel measures Wasm execution work, including the
guest Python interpreter. Exceeding a budget or trapping closes the affected mod
and removes its owned registrations. Guest components receive no host WASI
filesystem, network or process environment.

## Architectural overview

### Launch and process ownership

`launcher/entry.py` verifies the bundled interpreter, dependency inventory and
trusted Python runner under `libs/mod-runners`, then validates Steam's command
against the installed `TeamPrincipal.exe`. `--check-only` verifies dependencies
and the runner without starting the game. `process_lifecycle.py` holds a
controller lock, starts the game through Frida and tracks child processes, exits,
replacement, exec transitions and PID reuse. Each tracked game session owns its
bridge and mod instances.

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
Normal launcher entry supplies the verified runner for schema 2 Python source
admission and explicit schema 1 Wasm compatibility. Source snapshots must match
admitted metadata; source preparation is cancelled when its session closes.
Both profiles use the same progress, native readiness and session ownership.
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
providers or rejection callbacks before sandbox instantiation. The installed
bundle retains its source, upstream notice texts and a bounded integrity
inventory. One fixed runner artifact supplies a separate instance for each mod;
source enters guest memory and executes there without per-mod component builds.
`mod_runtime/source.py` validates source manifests and snapshots bounded Python
source as inert text, using pinned directory handles and ordinary-file checks.
`mod_runtime/runtime.py` transfers snapshots into the trusted runner, shares
bounded fuel and time across source initialization, and owns lifecycle cleanup.
Activation and event calls retain the sandbox's ordinary per-call limits.
`launcher/source_mod.py` adapts this runner to the existing mod lifecycle, JSON
event validation and host-owned diagnostic/menu identity.
`mod_runtime/preparation.py` snapshots source and compiles verified runner bytes
on a caller-owned worker, with controller adoption and cancellation cleanup.

The release workflow builds the fixed trusted runner once using the pinned
developer componentizer. `build_frida.py` produces the reduced native transport;
`frida_sources.py` exports and restores matching build sources.
`prepare_release.py` assembles runtime dependencies and copies the verified
runner, trusted sources and notices into release staging. Dependency files are
inventoried in `bundled_packages.toml`; the runner carries its own exact inventory.
The test tree separates launcher, runtime and hook-manager behaviour, with owned
fixtures for component execution, native bridge sessions and packaged-runtime
probes.
