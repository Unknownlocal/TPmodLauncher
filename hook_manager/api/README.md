# Mod API contract architecture

This directory holds the versioned public WebAssembly Component contracts.
Each version defines a `code-mod` world with the same lifecycle exports:
`activate()` and `on-event(name, payload) -> string`. The event payload and result
are bounded JSON strings validated by the external launcher.

| Contract | Host interface |
| --- | --- |
| [0.1.0/mod.wit](0.1.0/mod.wit) | Retained diagnostics-only contract with `report(message)`. |
| [0.2.0/mod.wit](0.2.0/mod.wit) | `report(message)` and `add-menu-button(id, label, message)`, selected by WIT feature annotations. |
| [0.3.0/mod.wit](0.3.0/mod.wit) | Adds `register-settings(definition)` and `save-settings(values)` with the `ui-settings` capability; see the [UI settings contract](0.3.0/README.md). |

`launcher/mod_api.py` records accepted versions, capabilities and lifecycle
signatures. Schema 1 Wasm admission checks component imports against manifest
grants and requires the public export signatures. Feature selection controls
binding generation; runtime permission checks separately enforce access.

Schema 2 Python mods use API 0.2.0 or 0.3.0 through the prebuilt runner's `wit_world`
facade. That runner has a separate private ABI under `mod_runtime/0.2.0`, including
source loading. Its host-operation registry keeps scalar signatures and required
capabilities aligned with the public operations; Python mods do not generate
bindings or implement the private loading protocol.

The [hook manager overview](../README.md) describes native integration and the
implemented mod API. The WIT files define the callable boundary; they do not
supply game hooks, select gameplay events or grant access independently of the
manifest and trusted host providers.
