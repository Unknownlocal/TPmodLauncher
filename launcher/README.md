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

For Python mods, `SourcePreparation` copies every source member, checks its
previously accepted manifest and limits the combined size before reading runner
bytes once. Prebuilt Wasm components also read on a worker during normal startup.
The controller then requests compiled code through
`compiled_mod.py` and polls for completion without waiting on a compiler.

`compilation_registry.py` keeps loaded code and pending compilations for each
execution thread. Identical bytes and compiler settings reuse one entry before
another Engine or Component is created. `compiled_component.py` performs the
actual compilation on a worker. Preparation creates no Store or game registration.
Each request can be cancelled independently; the last reference frees the code,
or disposes an abandoned compiler result when it finishes.
Closing one loading scope lets compilation still needed by another scope finish.

`compilation_cache.py` configures Wasmtime's local cache under
`bin/wasmtime-cache` after checking ownership and access. Failed cache checks fall
back to uncached compilation. Every admission still verifies current bytes and
creates fresh instance state. `load_timing.py` measures loading phases; the pack
loader logs those timings and cache status.

`pack_candidate.py` holds checked metadata. The controller creates one
`SharedPythonRunner` for all source candidates. All sources transfer before
entry imports, so a member can import a peer immediately. Imports happen together
at the first source candidate; source and prebuilt activation stays in ID order.
Source UI declarations stay staged until pack startup finishes. A failed source
member rejects the whole fixed group; the loader does not retry with fewer mods.

## Mod execution and native readiness

`mod_api.py` records public API versions, permissions and lifecycle signatures;
`mod_contract.py` supplies prebuilt Wasm component admission, bounded JSON events and host-owned
diagnostic and UI resource identity. `shared_source_mod.py` applies that lifecycle
and event contract to each shared entry. `runner_host.py` checks each owner's
permissions and fixes its host callbacks before shared routing is created.

`compiled_code.py` holds the shared Engine and compiled component after adoption.
`PackMods` uses one Store for the source group and separate Stores for prebuilt
components. A whole execution phase belongs to one Store at a time, so its
timeout cannot interrupt another Store using the shared Engine.

`deadline_watchdog.py` reuses one waiting thread per shared Engine. It interrupts
overdue guest execution and clears each finished call's deadline before another
mod can run. The thread stops before that Engine is freed.

`sandbox.py` owns a runner's linker, Store, shared-code reference and cleanup. It
validates imports and exports, serialises guest entry on the owning thread, and
enforces memory, fuel, deadline and host-call limits. Shared source loading gives
each owner's transfer and entry import separate source allowances, plus one
allowance for sealing the complete source table. Activation and events use
ordinary call budgets.
A failure closes the affected Store and runs its cleanup.

`shared_runner_host.py` supplies the host-call routing for
`mod_runtime.shared_runtime.SharedPythonRunner`. One shared Store serves several
logical source mods. Each source module imports a `wit_world` API bound to
its admitted mod ID; the host selects that ID's provider table for diagnostics,
menus, settings and themes. Other source IDs and ownerless calls are rejected.
These tables retain ordinary permission checks, while mods sharing Python can
borrow one another's API modules. They provide routing, not mutual isolation.
The group owns closure and cleanup. Closing a source entry closes the whole group,
revokes every member's registrations before replacement UI is sent, and logs
the affected IDs once. Unactivated peers are included in failure counts.

Trusted calls may supply an absolute deadline to `Sandbox.phase`. It cannot
extend the phase's normal allowance. Expired requests are rejected before guest
entry; work that overruns after entry closes its Store. No background job or
parallel source execution is involved.

The loader publishes progress and staged UI declarations to the native adapter.
Startup readiness waits for preparation outcomes, complete source activation and
native acknowledgement. Immediate and deferred native rejection use the same
cleanup. Session closure reports logical mods in reverse activation order;
loading work, native UI ownership and bridge lifetime remain tied to that session.

[`regulations/`](regulations/README.md) reads enabled source mods' definitions and
binds their Python validators once in the pack's shared Wasm runner. Native fields
are preserved; conditions stay in mod code. An authenticated connection carries
checks to the controller thread under one absolute deadline.

`RegulationRuntime` owns career preparation, installation, activation and
retirement. The bridge sends fixed requests; `PackMods` derives readiness and UI
holds from runtime status. Loading protection must be armed before career
selection opens, and activation acknowledged before mod UI appears. Returning to
the menu or cancelling a load retires career hooks and data while retaining the
pack's interpreter, handlers and connection. The next career needs fresh data and
hook approval. The child package documents failure handling and required owners.
Source API `0.4.0` grants the `regulations` permission; native setup and the callable
adapter remain private.

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

`Package-Release.ps1` coordinates developer setup and `release_workflow.py`: pinned Frida source
fetching and compilation, matching source export, isolated runner/dependency
preparation, and ZIP assembly. No pre-existing binary cache is required; the
checkout's selected dependencies and active runtime are preserved.
Its platform selection keeps the Windows and Linux/Proton archives and candidate
records separate, including during preparation and verification.
