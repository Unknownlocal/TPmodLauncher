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
| `api` | Exact source API version `0.2.0` or `0.3.0`. |
| `runtime` | `python`. |
| `entry` | Dotted module name under `src/`, at most 128 characters; `mod` loads `src/mod.py`. |
| `capabilities` | Unique permissions: `diagnostics`, `main-menu`, and (API `0.3.0` only) `ui-settings`. An empty list is valid. |

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

API `0.3.0` also supplies `register_settings(definition)` and `save_settings(values)`
under the `ui-settings` permission. These stage labelled colour fields and own
the mod's local `settings.json`; see the [UI settings contract](hook_manager/api/0.3.0/README.md).
The native editor and custom palette application are not integrated yet.

`on_event(name, payload)` receives a named event and a JSON payload string, and
returns a JSON response string. The host validates plain JSON values, with a depth limit of 8, a 1,024-node budget and a 4,096-byte
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

The launcher runs outside the game. It owns mod discovery, compilation and
sandboxed execution; a trusted bridge supplies the in-game adapters.

| Area | Responsibility |
| --- | --- |
| [launcher](launcher/README.md) | Verified startup, game-process tracking, pack loading, compilation cache, mod lifetimes and release preparation. |
| [hook_manager](hook_manager/README.md) | Main-thread game integration, owned hooks, pack discovery, native menu adaptation and the current mod API. |
| [mod_runtime](mod_runtime/README.md) | The prebuilt Python runner, source validation, worker preparation and per-mod execution state. |
| [tests](tests/README.md) | Behavioral checks, real Wasm execution, owned native helper processes, bundled-runtime probes and CI coverage. |

`launcher/entry.py` verifies the bundled interpreter, dependency inventory and
trusted runner before accepting Steam's game command. The controller holds one
launcher lock, tracks game children and replacement processes, and creates a
bridge and mod scope for each session.

The bridge enters the game's CPython 3.13 runtime on its main thread with the GIL
held. Fixed launcher-owned sources provide pack discovery and native menu
adapters. Status, loading and declarative menu records cross this boundary;
author Python executes inside Wasmtime in the external launcher.

The pack loader validates manifests and duplicate IDs before execution. A worker
snapshots Python source and compiles verified runner bytes, or prepares an
explicit Wasm component. The controller adopts the result and creates fresh
permissions, a Store and guest state. Wasmtime's local compilation cache can
reuse compiled code under `bin/wasmtime-cache`; input verification and fresh
instance ownership still apply on every load.

One prebuilt runner artifact serves separate Python instances for individual
mods. The runner imports source from guest memory and exposes diagnostics and
menu declarations through granted host functions. The sandbox enforces signatures,
fuel, deadlines, host-call counts and guest linear-memory limits. Loading progress
and native registration acknowledgements control startup readiness. Session
closure cancels unfinished preparation, closes mods in reverse activation order,
removes their menu registrations and restores owned hooks.

Release tooling builds the fixed runner with the pinned developer componentizer,
prepares the bundled interpreter and dependencies, and retains trusted sources
and upstream notices. Dependencies use `bundled_packages.toml`; the runner has
its own exact file inventory. Players use the prepared artifacts without a
componentizer or per-mod Wasm build.
