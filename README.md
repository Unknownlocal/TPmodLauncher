# modLauncher

## Licence and attribution

- [Licence](LICENCE)
- [Attribution](ATTRIBUTION.txt)

## Install and run the launcher (Windows)

Keep `modLauncher` beside `TeamPrincipal.exe` in the game folder. Download the
prepared release ZIP, including `libs` and `bundled_packages.toml`. It includes
Windows x64 Python and its dependencies, so you do not need to install Python,
pip or packages. The source-only download does not include the runtime.

Close the game before installing. In Steam, choose **Manage > Browse local files** and extract the
ZIP there. Place `modLauncher/` beside `TeamPrincipal.exe`, with no extra folder
around it.

Open the game's **Properties > General > Launch Options** in Steam and enter:

```text
"<GAME_FOLDER>\modLauncher\libs\python-3.13.16-embed-amd64\python.exe" -B "modLauncher\launcher\entry.py" -- %command%
```

Replace `<GAME_FOLDER>` with the full path from **Browse local files**, it's the game executable's root path. Only `python.exe` needs an absolute path; keep
`modLauncher\launcher\entry.py` relative so the line fits Steam's launch options.
Keep the quotes, `--` and `%command%` as shown, then start the game through Steam.
Steam supplies the game executable and its arguments after `--`.

Closing the launcher console or pressing Ctrl+C also asks the game to close,
as if you had clicked its Close button.
If the game has not exited after four seconds, the launcher force-closes its
remaining game processes.

For help with the path, double-click the release ZIP's optional
`modLauncher/SteamLaunchOptions.cmd` helper. It generates your launch line and
copies it to the clipboard; press **Ctrl+V** in Steam's Launch Options field.
It also displays the line for manual copying and reports whether copying worked.
It uses bundled Python without changing Steam settings or starting the game.
Packaging generates this `.cmd` file; it is absent from the source checkout.

Clear Steam's Launch Options to restore normal startup. To update, close the game
and launcher, replace the whole `modLauncher` folder, and update the launch line
if the Python path changed. Installed mods stay in your data packs.

Select the Formula data pack with your code mods. The launcher loads the game's
startup pack and keeps running to track the game and replacement processes.
Restart the game to load changed mods or a different pack.

Loading results and mod diagnostics go to `modLauncher/logs/launcher.log`, replaced
on each launcher run. The native menu shows progress and blocks season actions
until code loading and menu registration settle.

### Linux through Proton/Wine (experimental, untested)

Use the Windows release bundle to run bundled Windows Python and the game in the
same Proton/Wine environment. This approach needs no native Linux Python, and none
is included. Game support under Proton does not confirm launcher support: Frida
injection, child tracking and mod loading are untested there.

In Linux Steam, enter this launch line with your Linux game path as `<GAME_FOLDER>`:

```text
bash "<GAME_FOLDER>/modLauncher/launcher/launch_proton.sh" %command%
```

To generate the line, run the Linux helper from the game folder:

```bash
bash modLauncher/SteamLaunchOptions.sh
```

It displays the quoted line and reports whether clipboard copying worked. It uses
`wl-copy`, `xclip` or `xsel` if installed; otherwise, copy the line manually.
Linux Python is not needed.

The wrapper keeps Steam's Proton version, compatibility prefix and game arguments,
and starts bundled Windows Python in that environment. Enable Proton for the game
in Steam. Fixture tests cover this launch route; actual Proton injection and mod
loading remain untested. The helper does not configure plain Wine outside Steam.

### macOS

macOS is unsupported and untested.

### Package a Windows or Linux/Proton release

From the repository root, run:

```powershell
.\Package-Release.ps1 -Version 0.1.0
# For Linux/Proton users, from the same Windows machine:
.\Package-Release.ps1 -Version 0.1.0 -Platform linux
```

The script sets up the developer environment, downloads and builds the pinned
Frida sources, exports their source archive, builds the trusted runner, prepares
bundled Python and dependencies, and checks the extracted ZIP. It writes the ZIP
and SHA-256 checksum to `dist/`, keeping existing versioned outputs. Platform
names distinguish the archives: `modLauncher-0.1.0-windows-x64.zip` and
`modLauncher-0.1.0-linux-proton-x64.zip` can coexist without a version bump.
Both contain the bundled Windows runtime; Linux users run it through Proton/Wine.

To package already prepared files only, without builds or downloads:

```powershell
.\.venv\Scripts\python.exe -B launcher/package_release.py --version 0.1.0 --platform linux
```

Use `--platform windows` for the Windows archive (the default). Repeating the
same version and platform preserves existing output and reports a collision.

No cache or launcher installation is needed. Use `-PrepareOnly` to build and check
a candidate without a ZIP. Use `-CheckOnly` to recheck that version's last prepared
candidate without setup or downloads. Developer requirements, WSL usage and
optional overrides are in the
[packaging guide](launcher/PACKAGING.md).

## Instructions for modders

### Python source mods

Start with the [diagnostic example](examples/diagnostic_mod),
[menu example](examples/menu_mod) or [theme editor](examples/theme_mod) for shared
UI settings. Write a `mod.toml` manifest and readable Python in `src/mod.py`:

```toml
schema = 2
id = "my.mod"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics", "main-menu"]
```

All seven fields are required; extra fields are rejected.

| Field | Accepted value |
| --- | --- |
| `schema` | Integer `2`. |
| `id` | Unique ID, up to 128 characters. Start with a lowercase letter; use lowercase letters, digits, `.`, `_`, `:` or `-` after it. |
| `version` | SemVer version, at most 64 characters. |
| `api` | Exact source API version `0.2.0`, `0.3.0` or `0.4.0`. |
| `runtime` | `python`. |
| `entry` | Dotted module name under `src/`, up to 128 characters; `mod` loads `src/mod.py`. |
| `capabilities` | Unique permissions: `diagnostics`, `main-menu`, `ui-settings` (API `0.3.0`/`0.4.0`) and `regulations` (API `0.4.0`). An empty list is valid. |

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

`activate()` runs once after the mod is accepted. `report()` needs `diagnostics`;
`add_menu_button()` needs `main-menu`. Request only permissions your mod uses.
The runner supplies `wit_world.WitWorld`, `report()` and `add_menu_button()`;
mods do not generate or distribute API bindings.

Register each menu item with an ID, label and confirmation message. IDs follow
the manifest ID rules. Labels use 1-64 printable characters; messages use 1-160.
Neither may start or end with whitespace. Each mod can register four buttons,
up to 32 per game process. IDs must be unique within the mod; labels must be
unique across registered and existing native items. Clicking a button shows its
confirmation in the native menu for 2.5 seconds.

API `0.3.0` adds `register_settings(definition)` and `save_settings(values)` with
the `ui-settings` permission. These add labelled colour fields to the mod's
Options tab and save values in its local `settings.json`. Successful native saves
send `ui-settings.changed` to the owning mod. The same permission adds
`register_theme(definition)` for named palettes in the Color Pack dropdown.
Choices and saved palette edits apply after a restart. The
[implemented mod API](hook_manager/README.md#ui-settings-and-palettes) defines
JSON formats, ownership and limits.

`on_event(name, payload)` receives an event name and JSON payload string, then
returns a JSON response string. The host checks plain JSON values: up to 8 levels
deep, 1,024 nodes and 4,096 UTF-8 bytes per interface string. General gameplay
events are not implemented; menu clicks show their declared confirmation message.

Authors supply the manifest and source together in a mod folder. Install it
directly inside the selected Formula data pack:

```text
<game-root>/
  TeamPrincipal.exe
  modLauncher/
  data/<selected-data-pack>/
    my_mod/
      mod.toml
      src/mod.py
```

At startup, the launcher loads your Python source into its bundled trusted Wasm
runner. Authors and players need no component build tool, generated bindings or
`mod.wasm`. The launcher reads your manifest; it does not generate one.

Add Python modules and packages under `src/`. Packages need `__init__.py`;
module names must be portable ASCII Python identifiers. Paths must be unambiguous
on both case-sensitive and case-insensitive systems. Top-level names must not
shadow the runner API or Python standard library. The source snapshot accepts
only UTF-8 `.py` files: up to 1 MiB across 256 files, 512 tree entries, 16 path
levels and 256 characters per relative path. Third-party packages and native
extensions are not bundled. The frozen guest standard library includes `json`,
`math`, `os` and their runtime dependencies. A module's availability does not
grant access to host files, networking or processes.

All Python source mods in a selected pack share one interpreter and memory.
Ordinary imports resolve within your own `src/`. To share another enabled mod's
module, use `wit_world.import_mod("other.mod", "module_name")`; it returns that
mod's existing module object. Mod authors are responsible for compatibility
between shared Python objects. All source files are available before any entry
imports, and activation runs in mod-ID order alongside prebuilt components.
The group accepts at most 8 MiB of source across 4,096 files; the pack admits
at most 32 code mods. A failed source member closes all Python source mods in
that pack. Mods with prebuilt Wasm components keep separate runtime instances.

#### Regulation validators (API `0.4.0`)

API `0.4.0` keeps the earlier source operations and adds the `regulations`
permission. Start with the [rookie-contract example](examples/rookie_contract_mod).
Authors supply its `mod.toml`, Python under `src/` and regulation declarations;
the launcher supplies the runner, game adapters and validation connection.

Install the example's code folder beside other mods in the selected Formula
pack. Merge its [`regulations.json`](examples/rookie_contract_mod/regulations.json)
`mod_extensions` entries into the pack's existing **root** `regulations.json`.
Keep the native fields and other mods' entries; do not replace the whole file.
The copy inside the code folder is an example fragment, not a second loaded file.
Each entry is keyed by the exact manifest ID and requires `schema: 1`.
Sections whose code mod is absent or lacks the permission are ignored beyond
JSON syntax and the whole-file size limit. Removing the code folder disables
its section on the next game start.

A `validators` entry names a rule, a supported scope and a Python function:

```json
{"id":"rookie_length","rule":"example.rookie_contracts.length","scope":"personnel.contract","handler":"mod.validate_contract"}
```

Rules can use native categories or categories declared by that mod. Custom
category keys must begin with the mod ID and `.`. Current scopes are
`personnel.approach` (driver and staff approaches to Formula teams),
`personnel.contract` (driver, staff, academy and staffing proposals) and
`development.project` (aero and chassis proposals). The launcher chooses the game methods; declarations
cannot name arbitrary functions or call the private callable adapter.

Approach validators receive person and employer identities, the intended `role`,
and `action: "approach"` in `context.proposal`. The common clock and effective
season/start fields remain available; the end week is `null`. There are no
salary or duration fields at this stage. Register a validator separately for
each scope it supports. A refusal prevents the approach and uses the game's
own failure feedback: a dialog for drivers, inline text and error sound for staff.

Write a synchronous `validate_contract(context, regulation)` function in the
named source module. It receives fresh dictionaries and lists, and returns a
dictionary such as `{"decision": "allow"}` or
`{"decision": "deny", "message": "Contract is too long"}`. A refusal needs
1-160 printable characters without surrounding whitespace. This is separate
from `WitWorld` lifecycle methods and `on_event()` JSON-string responses.

`context` describes the proposed action, its initiator, career clock and effective
season. `regulation` supplies `current`, optional `upcoming` and `selected`.
Use `regulation[regulation["selected"]]` for the option applying to that proposal;
its `parameters` contain the rule values and `option_data` contains the mod's
extra data for that option. The example reads `max_years` from those parameters
and uses `proposal.native_checks.rookie_contract_length_applies`, preserving the
game's own definition of a rookie.

The optional `native_rules` switches `rookie_contract_length_check` and
`staff_prestige_check` keep their native checks when omitted or true; false
requests a bypass. A switch can instead refer to a boolean parameter in an owned
category, for example:

```json
{"rookie_contract_length_check":{"rule":"example.rookie_contracts.length","parameter":"native_check"}}
```

Every option must supply that boolean. The adopted option for the proposed
contract's season chooses whether the native check runs. Only one enabled mod
may declare each switch; conflicts reject preparation. The example offers no
rookie length restriction, native limits, or a mod-enforced four-year limit.
Other native refusals still apply. Saved accepted contracts take effect normally
without another mod check.

The pack file is limited to 1 MiB. Each complete context-plus-rule argument is
limited to 4,096 UTF-8 bytes, 1,024 nodes and eight container levels; checks share
one 100 ms action deadline. Native rule choices stay in memory between checks.
See the [declaration and execution limits](launcher/regulations/README.md) for
category, validator and script bounds. Invalid declarations or unavailable
required validators keep the pack's mod checks unready; loading failures are
reported in the CLI and launcher log.

### Mods with prebuilt Wasm components

Authors build and distribute the component; the launcher loads the binary
without compiling its source. Any language with a compatible WebAssembly
Component Model toolchain follows the same contract-based build steps:

1. Choose API `0.1.0`, `0.2.0` or `0.3.0` and use its
   `hook_manager/api/<version>/mod.wit` file. See the
   [mod API overview](hook_manager/README.md#current-mod-api-overview) for operations.
2. Generate your language's bindings for the `code-mod` world. Enable only the
   WIT features matching your manifest's capabilities: `diagnostics`,
   `main-menu` and, for API `0.3.0`, `ui-settings`. API `0.1.0` supplies diagnostics
   directly and has no feature switches.
3. Export exactly the two lifecycle functions `activate()` and
   `on-event(name, payload) -> string` using those bindings.
   Event payloads and results are JSON strings; return `"null"` when no response
   data is needed. Import only host operations your mod is allowed to use.
4. Compile and package the result as a **Wasm component**, saved as `mod.wasm`.
   A core Wasm module alone will not work. If your compiler emits one, use your
   toolchain's component packaging stage. Omit or stub unused WASI dependencies
   at build time: the launcher supplies no WASI imports. Components must fit the
   32 MiB file and 64 MiB guest-memory limits, and initialise within 5,000,000
   fuel units and one second.
5. Supply this `mod.toml`, matching `api` and `capabilities` to the contract and
   features used for the build:

```toml
schema = 1
id = "my.wasm.mod"
version = "0.1.0"
api = "0.2.0"
runtime = "wasm"
component = "mod.wasm"
capabilities = ["diagnostics", "main-menu"]
```

All seven fields are required; extra fields are rejected. IDs and versions follow
the Python source mod rules; capabilities must match the chosen component
contract. Prebuilt components cannot request `regulations`. `component` replaces `entry`
with a relative `.wasm` path inside the mod folder. Use `runtime = "wasm"` for
any implementation language. The launcher rejects unsupported imports and missing
or mismatched lifecycle exports.

Install and distribute the manifest and binary together:

```text
<game-root>/
  TeamPrincipal.exe
  modLauncher/
  data/<selected-data-pack>/
    my_wasm_mod/
      mod.toml
      mod.wasm
```

The installed mod folder needs no author source or build tools. Start the game
through the launcher and check the log for acceptance and activation.

### Loading and execution

Several code mods can share a data pack. Valid mods activate in ascending mod-ID
order; all mods with a duplicate ID are rejected. Discovery accepts up to 4,096
direct pack entries and 32 candidate mod folders. Linked folders and files are
rejected or skipped. Manifests can be up to 16 KiB. Source loading never falls
back to `mod.wasm`. Invalid manifests are rejected before grouping; a failure
loading an admitted source member rejects its whole group.

Check the launcher log for `CODE MOD ACTIVE`, `CODE MOD REPORT` and
`CODE MOD MENU` records. Rejections name the affected mod. Each process gets fresh
mod state; shutdown or process replacement releases its mod instances.

The source group shares Python state and one 64 MiB guest linear-memory limit.
Each prebuilt component has its own state and 64 MiB limit. Python initialisation
allows 20,000,000 fuel units and five seconds. Each owner's source transfer and
entry import have separate allowances of 256,000,000 units and five seconds;
sealing the complete source table has another allowance of that size. Activation
and each event allow 5,000,000 units and one second. Each phase allows up to
64 host calls. Prebuilt components use that smaller budget for initialisation.
Fuel measures Wasm work, including the guest Python interpreter. A budget overrun
or trap closes the affected runtime and removes all its registrations, including
every source peer sharing it. Guest components have no host WASI
filesystem, network or process environment.

### Use the theme example

Install `examples/theme_mod` as a mod folder in the selected Formula data pack.
Its **Theme** tab in Options has 52 colour groups with hex inputs and live
swatches. Click a row to edit; Tab and Shift+Tab change rows, Ctrl+A selects text,
Ctrl+V pastes and Enter saves. Scroll for more rows. Invalid values disable Save.
RGB fields use `#RRGGBB`; the overlay uses `#RRGGBBAA`.

**Save** writes the mod's `settings.json`. **Reset to Defaults** sets defaults
for saving; unsaved edits stay when switching tabs in that Options screen.
Select **Custom Theme** under **Graphics > Color Pack**, confirm **Save choice**,
then restart through the launcher. Later colour edits also need a restart.
Selecting a built-in pack restores it on the next launch; removing the mod
restores the game's default palette.

## Architectural overview

The launcher runs outside the game. It finds, compiles and runs mods in a sandbox;
a trusted bridge supplies the in-game adapters.

| Area | Responsibility |
| --- | --- |
| [launcher](launcher/README.md) | Startup checks, game-process tracking, pack loading, compilation cache, mod lifetimes and release preparation. |
| [hook_manager](hook_manager/README.md) | Main-thread game integration, owned hooks, pack discovery, native menu/settings/theme adapters and the current mod API. |
| [mod_runtime](mod_runtime/README.md) | Prebuilt Python runner, source checks, worker preparation and shared Python execution. |
| [tests](tests/README.md) | Behaviour checks, real Wasm execution, owned native helper processes, bundled-runtime checks and CI coverage. |

`launcher/entry.py` checks bundled Python, the dependency inventory and trusted
runner before accepting Steam's game command. The controller holds one launcher
lock, tracks game children and replacement processes, and creates a bridge and
mod scope for each session.

The bridge enters the game's CPython 3.13 runtime on its main thread with the GIL
held. Fixed launcher-owned sources find packs and provide native UI adapters.
Status, loading, menu declarations, settings and palettes cross this boundary;
mod authors' Python runs inside Wasmtime in the external launcher.

The pack loader checks manifests and duplicate IDs before execution. A worker
copies all source mods and prepares one verified runner compilation request,
or prepares an explicit Wasm component. The controller creates one shared
Python interpreter per pack and a separate Store for each prebuilt component.
Wasmtime can reuse compiled code from `bin/wasmtime-cache`; every load still
checks inputs and creates fresh instance ownership.

The shared runner imports source from guest memory and provides diagnostics,
menu declarations, UI settings and palettes through permitted host functions.
The launcher saves settings and sends save notifications; native adapters handle
the editor and apply palettes
on restart. The sandbox enforces signatures, fuel, deadlines, host-call counts
and guest linear-memory limits. Loading progress and native registration
acknowledgements control startup readiness. Closing a session cancels unfinished
preparation, closes owned runtimes, removes their native UI registrations and
restores owned hooks. Logical mod closure is reported in reverse activation order.

For regulation mods, the launcher reads enabled declarations from the pack's
`regulations.json` and keeps checked rule data in the shared runner. Fixed game
adapters prepare proposed contracts or development projects and request mod
validation. Python handlers receive copied proposals and the selected rule data;
their decisions gate the action while the game's other checks remain in force.
The native callable adapter stays private.

Release tools build the fixed runner with the pinned developer componentizer,
prepare bundled Python and dependencies, and keep trusted sources and upstream
notices. Dependencies use `bundled_packages.toml`; the runner has its own exact
file inventory. Packaging selects these files, original launcher sources and
matching Frida build/source materials, generates the Steam launch-options helper,
and checks the extracted Windows bundle before writing the release ZIP and
checksum. Players use the prepared files without a componentizer or per-mod
Wasm build.
