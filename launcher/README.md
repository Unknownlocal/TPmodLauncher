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

## Profiling catalogue

`profiling_catalogue.py` provides an internal, data-only catalogue for the planned
diagnostics profiling API. Trusted providers register admitted source bundles,
Python source snapshots or `CompiledSource` code objects. Discovery compiles but never executes/imports source;
content fingerprints and a bounded JSON cache reuse metadata across sessions.
Compiled code is fingerprinted with write-only serialization; the cache stores
descriptors, never executable bytecode. Providers must verify artifact ownership
and supply display locations rather than trusting code filenames.
Owners and live bindings are rebuilt separately. Binding changes invalidate
catalogue cursors, and queries use exact selectors and bounded UTF-8 pages.
Trusted admission uses `resolve()` to copy matched metadata, opaque runtime
binding tokens and unmatched selectors into the runtime's frozen-plan layer.
String tokens retain their generation when relayed through JSON unchanged.
`apply_bindings()` accepts trusted runtime-provider readiness batches, validating
every update before changing catalogue state. Process/artifact/generation checks
reject stale replacements; exact state retries preserve generations and cursors.
Compiled catalogue IDs and runtime verification share the same source identity
calculation, so equal names/locations cannot admit a different function body.

The service installs no hooks and is not yet wired to pack loading, the game
bridge, WIT or the runner facade. The compiled-code inventory and safe namespace
lookup helper, verified provider handshake and synchronous binding/plan layer are available; actual loader,
race-provider and guest-runtime wiring remain integration work. Source grammar follows the host Python
version; unsupported source discovery must not be treated as complete coverage.
The metadata cache reuses the pinned-directory atomic JSON writer; the fixed
host-owned cache leaf is separate from mod settings.

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
