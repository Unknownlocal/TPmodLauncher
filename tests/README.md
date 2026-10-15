# Test architecture

The pytest suite mirrors the production split between the external launcher and
the in-game hook manager. `launcher/` contains dependency, packaging, process
lifecycle, component, cache and sandbox tests. `hook_manager/` contains manager,
pack discovery, native menu and bridge tests.

`mod_runtime/` checks trusted runner builds, private ABI compatibility, complete
bundle inventories, per-mod permission enforcement and relocated bundled-interpreter
execution without compiler packages. Build fixtures are separate from player
execution, and the existing production sandbox supplies execution limits.

The root `conftest.py` supplies temporary projects, synthetic dependency wheels,
runtime copies and a small owned native-menu interface. `launcher/conftest.py`
adds real Git source repositories and Wasmtime components, and builds the public
Python examples once per session. Fixtures stay separate from game files and
external services.

Small WAT components exercise admission, permissions, limits, callback failures
and cleanup. Built Python components exercise generated WIT bindings, private
guest state, denied host I/O and the author build path. Cache tests combine real
Wasmtime compilation with filesystem and platform access checks. Pack-loading
tests combine manifests and components with controlled process and bridge events
to exercise ordering, cancellation, readiness and per-session ownership.

Native integration tests run the real Frida controller and trusted bridge
against owned CPython 3.13 helper processes. Their targets model module imports,
menu interfaces and process replacement. Embedded-runtime tests relocate a
prepared Windows bundle and invoke its production entry point in subprocesses,
covering dependency verification, import isolation and component execution.

The `native`, `embedded` and `frida_build` markers identify tests requiring native
transport, the packaged Windows interpreter or a reduced Frida build. Portable
jobs cover Windows, Linux and macOS; native acceptance uses the supported
Windows and Linux transports. Required acceptance modes turn missing native
prerequisites into failures.

`pyproject.toml` defines collection, markers and line/branch coverage for
`launcher`, `hook_manager` and `mod_runtime`. The pytest workflow separates launcher,
hook-manager, mod-runtime, native and embedded groups and combines their coverage artifacts.
The coverage workflow consumes those artifacts, while the reduced-Frida workflow
builds and checks platform transport candidates.
