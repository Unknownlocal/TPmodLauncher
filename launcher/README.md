# Launcher architecture

The launcher is the external controller for the game and its code mods. It owns
startup verification, process tracking, mod loading and sandbox execution. The
game receives fixed trusted adapters from `hook_manager`; mod source executes in
Wasmtime inside the launcher process.

## Startup and dependencies

`entry.py` verifies the bundled Windows x64 interpreter and dependency files
through `bundled_dependencies.py`, registers the trusted source packages, and
verifies the runner under `libs/mod-runners`. Its check-only path stops after
verification. Normal startup also validates Steam's command against the installed
`TeamPrincipal.exe` before creating the controller, bridge and mod scopes.

Dependency verification uses the expected versions and file inventory in
`bundled_packages.toml`. The runner's sources, notices and Wasm bytes have a
separate receipt. These inventories check the installed distribution's integrity;
normal startup does not download packages or build components.

## Process and bridge ownership

`process_lifecycle.py` holds the controller lock, starts the game through Frida
and tracks children, exits, exec transitions, PID reuse and replacements. Each
session owns one bridge and mod-loading scope. Replacement processes receive
fresh scopes; shutdown closes them and releases the controller's resources.

`game_bridge.py` supplies the fixed Python adapters and JavaScript transport from
`hook_manager`. The transport waits for CPython 3.13 and main-thread readiness,
then exchanges bounded status, pack, loading, menu, settings and palette data.
Source digests and session tokens identify the in-game manager owner.

## Pack loading and preparation

`pack_mods.py` scans direct children of the selected startup Formula pack. It
bounds discovery, validates manifests and rejects every candidate sharing a
duplicate ID before activating valid mods in ID order. Normal entry enables
Python source mods and mods with prebuilt Wasm components.

For Python mods, `mod_runtime.preparation` snapshots source, checks it against the
admitted metadata and compiles verified runner bytes on a worker. Wasm components
use `compiled_mod.py`. Preparation creates no Store or game registration; the
controller adopts completed work. Closing a loading scope cancels queued work
and disposes completed or late results.

`compilation_cache.py` configures Wasmtime's local cache under
`bin/wasmtime-cache` after checking ownership and access. Failed cache checks fall
back to uncached compilation. Every admission still verifies current bytes and
creates fresh instance state. `load_timing.py` measures loading phases; the pack
loader logs those timings and cache status.

## Mod execution and native readiness

`mod_api.py` records public API versions, permissions and lifecycle signatures;
`mod_contract.py` supplies prebuilt Wasm component admission, bounded JSON events and host-owned
diagnostic and UI resource identity. `source_mod.py` applies that lifecycle and
event contract to the Python runner. `runner_host.py` binds the complete typed interface
to per-mod providers or permission-denial callbacks.

`sandbox.py` owns the compiled component, linker, Store and cleanup scope. It
validates imports and exports, serializes guest entry on the owning thread, and
enforces memory, fuel, deadline and host-call limits. Source initialization uses
an aggregate loading phase; activation and events use ordinary call budgets.
A failure closes the affected mod and revokes its owned registrations.

The loader publishes progress and menu declarations to the native adapter after
activation. Startup readiness waits for both preparation outcomes and native
menu acknowledgement. Session closure releases active mods in reverse order;
loading work, native UI ownership and bridge lifetime remain tied to that session.

[`regulations/`](regulations/README.md) stages enabled-source regulation
definitions through a private, one-shot pack preparation seam. It preserves
native fields and keeps condition data opaque. Public capability admission,
sandbox handler binding and native publication remain gated by later steps.

[`json_ingest/`](json_ingest/README.md) provides bounded UTF-8 decoding, indexed
field selection and explicit duplicate policies. Document-specific adapters own
schema conversion and admission; regulations is the first consumer.

## Settings and palette ownership

`ui_settings.py` validates labelled colour fields and binds each mod to its fixed
`settings.json`. `_settings_files.py` pins ordinary directories and uses atomic
replacement for persistence. Registration returns saved values or defaults;
saved files survive instance cleanup, while declarations remain session-owned.

`pack_mods.py` stages settings and palettes for the trusted native adapters.
It validates native save requests against the owner and registry generation,
persists values, acknowledges the result and delivers `ui-settings.changed` to
the owning instance. Palette declarations use `hook_manager.themes.ThemeRequests`
and are published separately from settings. The native theme adapter owns
Color Pack choices and applies the selected palette at startup.

## Build and release boundaries

`build_frida.py` builds the pinned reduced native binding. `frida_sources.py`
exports and restores its matching build inputs. `mod_runtime.build` produces the
fixed trusted Python runner; `build_mod.py` retains the legacy component builder.
These are developer tools, separate from player startup.

`prepare_release.py` verifies the installed runner before dependency preparation
and copies accepted runner bytes, sources and notices into fresh release staging.
It prepares dependency generations and their inventory for the bundled
interpreter. Exported Frida rebuild tools remain independent of the launcher
runtime; runner loading is confined to release operations that need it.

`package_release.py` assembles a verified player ZIP and checksum from prepared
inputs on Windows or WSL. `--platform windows` (the default) and `--platform linux`
select separate Windows and Linux/Proton archive names, allowing both to use the
same release version. Both use the prepared Windows runtime. It verifies the pinned cached Python archive, selected
dependency inventory, runner and matching Windows Frida build/source receipts.
An explicit file selection includes trusted sources, upstream notices and Frida
source/relinking materials while excluding development and session data.
The command performs no downloads or builds.

Packaging generates `SteamLaunchOptions.cmd`, `SteamLaunchOptions.sh` and `VERSION.txt` inside the ZIP's
single `modLauncher/` directory. Before writing final output, it extracts the
archive, compares every file with the selected inputs and runs the extracted
entry point's native-import/runner checks. WSL invokes that same Windows
interpreter through interop. Failed checks leave no final archive; existing
versioned outputs are preserved.

`steam_launch_options.py` reads the installed dependency manifest to locate the
bundled interpreter, displays Steam's launch line and copies it to the clipboard.
If clipboard access fails, it keeps the line available for manual copying.
The generated command helper
invokes it without a system-Python fallback. Its interpreter path is absolute;
the entry-point path is relative to the game root. Neither helper changes Steam
settings or starts the game. [PACKAGING.md](PACKAGING.md) holds the packaging
commands and preparation requirements.

The Linux helper uses Bash and optional desktop clipboard tools without a native
Python runtime. Its Steam line invokes `launch_proton.sh`, preserving Steam's
Proton prefix and game arguments while substituting the bundled Windows Python.
`proton_entry.py` supplies the installed Wine-mapped executable path to the normal
entry point, retaining its target validation. This route is tested with owned
process fixtures; actual Proton mod loading remains untested.

Maintainers run `Package-Release.ps1 -Version <version>` from the repository root.
It coordinates developer setup and `release_workflow.py`: pinned Frida source
fetching and compilation, matching source export, isolated runner/dependency
preparation, and ZIP assembly. No pre-existing binary cache is required; the
checkout's selected dependencies and active runtime are preserved.
`-Platform linux` selects the Linux/Proton archive and a separate candidate
record; `-PrepareOnly` and `-CheckOnly` apply to the selected platform.
