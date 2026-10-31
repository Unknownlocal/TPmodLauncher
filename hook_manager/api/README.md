# Mod API contract architecture

This directory holds the versioned public WebAssembly Component contracts.
Each version defines a `code-mod` world with the same lifecycle exports:
`activate()` and `on-event(name, payload) -> string`. The event payload and result
are JSON strings whose size and contents are checked by the external launcher.

| Contract | Host interface |
| --- | --- |
| [0.1.0/mod.wit](0.1.0/mod.wit) | Retained diagnostics-only contract with `report(message)`. |
| [0.2.0/mod.wit](0.2.0/mod.wit) | `report(message)` and `add-menu-button(id, label, message)`, selected by WIT feature annotations. |
| [0.3.0/mod.wit](0.3.0/mod.wit) | Adds settings and palette operations under `ui-settings`; see the [contract architecture](0.3.0/README.md). |

`launcher/mod_api.py` records accepted versions, permissions and lifecycle method
types. The launcher checks a prebuilt Wasm component's imported operations against
its manifest permissions and checks that its exported methods match the public
contract. WIT features select which bindings are generated; permission checks
still apply when a mod calls the host.

Python source mods use API `0.2.0`, `0.3.0` or `0.4.0` through the trusted runner's
`wit_world` module. Source API `0.4.0` includes the earlier operations and adds
regulation handlers. It uses the runner's private interface, defined in
[`mod_runtime/0.4.0/runner.wit`](../../mod_runtime/0.4.0/runner.wit). It handles
source loading and regulation checks, and its host calls carry each mod's ID.
`mod_runtime/contract.py` records their argument types and required permissions.
Python mod authors supply source and a manifest; the launcher supplies the runner
and its bindings. API `0.4.0` has no public component contract: mods with prebuilt
Wasm components use the contracts listed above.

The [hook manager overview](../README.md) describes native integration and the
implemented mod API. The WIT files define the callable boundary; they do not
supply game hooks, select gameplay events or grant access independently of the
manifest and trusted host providers.
