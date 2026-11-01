# modLauncher

## Licence and attribution

- [Licence](LICENCE)
- [Attribution](ATTRIBUTION.txt)

## Install and run the launcher (Windows)

Close the game. In Steam, choose **Manage > Browse local files** and extract the
prepared release ZIP there. The `modLauncher` folder must sit beside
`TeamPrincipal.exe`, with no extra folder around it.

Use the release ZIP, which includes Python and its dependencies in `libs`.
The source-only download does not include the runtime.

In Steam, open **Properties > General > Launch Options** and enter:

```text
"<GAME_FOLDER>\modLauncher\libs\python-3.13.16-embed-amd64\python.exe" -B "modLauncher\launcher\entry.py" -- %command%
```

Replace `<GAME_FOLDER>` with the full path from **Browse local files**. Keep the
quotes, `--` and `%command%`, then start the game through Steam. The release's
optional `modLauncher/SteamLaunchOptions.cmd` helper can generate and copy this
line for you.

Select the Formula data pack containing your mods. Restart the game after
changing mods or selecting another pack. Loading results and mod messages appear
in `modLauncher/logs/launcher.log`, replaced on each run. The main menu waits
for mod loading before allowing season actions.

Closing the launcher console or pressing Ctrl+C also closes the game. After
four seconds, any remaining game processes are force-closed.

To update, close the game and launcher, replace the whole `modLauncher` folder,
and update the launch line if the Python path changed. Mods stay in your data
packs. Clear Steam's Launch Options to restore normal startup.

### Linux through Proton/Wine (experimental, untested)

Use the Windows runtime supplied in the Linux/Proton release. Enable Proton for
the game in Steam and enter this launch line, using your Linux game path:

```text
bash "<GAME_FOLDER>/modLauncher/launcher/launch_proton.sh" %command%
```

Or run the helper from the game folder:

```bash
bash modLauncher/SteamLaunchOptions.sh
```

The helper displays the line and copies it if a supported clipboard tool is
available. No Linux Python installation is needed. The wrapper uses Steam's
Proton version, compatibility prefix and game arguments. Game injection and mod
loading remain untested under Proton/Wine; plain Wine setup is not provided.

macOS is unsupported and untested.

### Package a release

From the repository root on Windows:

```powershell
.\Package-Release.ps1 -Version 0.1.0
# For Linux/Proton:
.\Package-Release.ps1 -Version 0.1.0 -Platform linux
```

The script prepares dependencies, builds the trusted runner and Frida, checks
the extracted bundle, and writes a ZIP and SHA-256 checksum to `dist/`.
Both platforms include Windows Python; Linux runs it through Proton/Wine.
Existing versioned outputs are preserved.

Use `-PrepareOnly` to prepare and check without creating a ZIP, or `-CheckOnly`
to recheck that version's last prepared candidate. To package prepared files
without builds or downloads:

```powershell
.\.venv\Scripts\python.exe -B launcher/package_release.py --version 0.1.0 --platform linux
```

Use `--platform windows` for the Windows archive. See the
[packaging guide](launcher/PACKAGING.md) for requirements and options.

## Instructions for modders

### Python source mods

Create a mod folder with these two files:

```text
data/<selected-data-pack>/my_mod/
  mod.toml
  src/mod.py
```

In `mod.toml`:

```toml
schema = 2
id = "my.mod"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics", "main-menu"]
```

Use a unique lowercase ID and a SemVer version such as `0.1.0`.
`entry = "mod"` loads `src/mod.py`. Request only the capabilities your mod uses.

In `src/mod.py`:

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

`activate()` runs once. `report()` writes to the launcher log.
`add_menu_button()` adds a button that displays its confirmation message when
clicked. `on_event()` receives and returns JSON strings; there is no general
gameplay event stream.

You supply the manifest and UTF-8 Python files under `src/`. The launcher
supplies the API and sandboxed Python runner. Additional packages need
`__init__.py`; third-party packages and native extensions are not bundled.

Start with the [diagnostic](examples/diagnostic_mod),
[menu](examples/menu_mod) or [theme](examples/theme_mod) example.

### Choose an API

| Feature | Python source API | Capability | Details |
| --- | --- | --- | --- |
| Log messages | `0.2.0`–`0.4.0` | `diagnostics` | [Host operations](hook_manager/README.md#host-operations) |
| Main-menu buttons | `0.2.0`–`0.4.0` | `main-menu` | [Host operations](hook_manager/README.md#host-operations) |
| Colour settings and palettes | `0.3.0`–`0.4.0` | `ui-settings` | [Settings API](hook_manager/README.md#ui-settings-and-palettes) and [theme example](examples/theme_mod/README.md) |
| Regulation checks and rule changes | `0.4.0` | `regulations` | [Regulation API](hook_manager/game_adapters/regulations/README.md) |

For complete operation formats and limits, see the
[mod API overview](hook_manager/README.md#current-mod-api-overview).
[Registration prerequisites](hook_manager/README.md#registration-prerequisites)
can make a registration wait for another mod, game module or career.

### Check game actions with regulations

Use Python source API `0.4.0` and request `regulations`. Start with an example:

| Example | What it checks or changes |
| --- | --- |
| [Rookie contracts](examples/rookie_contract_mod) | Rookie contract length. |
| [Team nationality](examples/team_nationality_mod) | Nationality for approaches and contracts, including academy signings. |
| [Annual payroll](examples/staff_spend_mod) | Team staff and driver salary caps. |
| [DRS voting](examples/regulation_vote_mod) | Teams' DRS voting calculations. |

Install the example's code folder in your Formula pack. Merge its
`regulations.json` fragment's `mod_extensions` entries into the pack's **root**
`regulations.json`, keeping existing fields and other mods' entries. The copy
inside the mod folder is an example; it is not loaded separately.
Each entry uses the exact manifest ID and requires `"schema": 1`.

Validators receive a copied proposal and the rule applying to its season.
They return an allow or deny decision; the game's remaining checks still apply.
Mods cannot call game methods or access live career records.

| API area | Available data and checks |
| --- | --- |
| [Shared personnel](hook_manager/game_adapters/personnel/README.md) | Approaches, offer checks, contract fields and optional payroll totals. |
| [Drivers](hook_manager/game_adapters/drivers/README.md) | Driver contracts and the native rookie-length check. |
| [Staff](hook_manager/game_adapters/staff/README.md) | Team and supplier contracts, team staffing and prestige requirements. |
| [Academy](hook_manager/game_adapters/academy/README.md) | Membership signings, renewals and annual fees. |
| [Projects](hook_manager/game_adapters/projects/README.md) | Aero, specification upgrades and chassis development. |
| [Regulations](hook_manager/game_adapters/regulations/README.md) | Handler format, rule selection, categories, automatic changes and voting. |

### Loading and troubleshooting

Restart through the launcher and check `launcher.log` for `CODE MOD ACTIVE`
or a rejection naming your mod. IDs must be unique. Mods activate in mod-ID order.

Python source mods in a pack share one interpreter and memory. Imports normally
stay within your own `src/`; `wit_world.import_mod("other.mod", "module_name")`
accesses an enabled peer's module. A source-mod runtime failure closes the whole
source group.

Mods have no host filesystem, network or process access. Execution time and
memory are limited; see the [execution boundary](hook_manager/README.md#execution-boundary)
and [source loading rules](mod_runtime/README.md#source-admission).

## Architectural overview

The launcher runs outside the game and executes mods in a Wasm sandbox.
Trusted adapters inside the game supply the supported menu, settings and
regulation features.

| Area | Responsibility |
| --- | --- |
| [launcher](launcher/README.md) | Startup, game-process tracking, pack loading, mod execution and release packaging. |
| [hook_manager](hook_manager/README.md) | Game integration and the public mod API. |
| [game adapters](hook_manager/game_adapters/README.md) | Prepare game-action data and apply regulation decisions. |
| [mod_runtime](mod_runtime/README.md) | Load Python source into the trusted runner and execute handlers. |
| [tests](tests/README.md) | Behaviour checks and CI organisation. |

Each game session owns its adapters and mod state. A selected pack's Python
source mods share a runner.
The launcher checks permissions and inputs before use, and removes owned game
changes when the session closes.
