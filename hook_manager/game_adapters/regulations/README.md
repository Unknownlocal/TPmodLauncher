# Private regulation game integration

`RegulationChannel` is the synchronous native client for launcher-owned regulation
dispatch. Its endpoint is trusted session configuration, never mod JSON. One
authenticated loopback connection carries length-prefixed JSON frames with
protocol v2, sequential request IDs and an absolute action deadline. It never retries failed
requests or reconnects an expired session. An identified handler failure latches
only that owner; protocol, disconnect or timeout failures close the channel.

`RegulationGuards` reuses the callable manager's wrapper ownership, worker
admission and restoration, but prepares one proposal for all subscribing mods.
The authenticated hello includes each owner's admitted `(rule, scope)` bindings
in ascending validator-ID order. Native adapters map those slots to one shared
context/selection table and make one channel call. Neither that metadata nor the
mod API can choose a native target or supply a host projection.

The trusted `select(rule, evaluation_season)` callback returns `selected`,
`current` and `upcoming` choice references from the published adopted policy.
It must use the same membership ordering as publication, not predict voting.
Selection runs once per distinct rule; proposal preparation runs once per action.
Projection, dispatch queueing and all decisions share the existing 100 ms budget.
Native execution follows only after all required decisions allow; native errors
propagate without retry. Validation failure keeps guards installed and unavailable.

`publish(native=..., custom=...)` streams a finite policy plan, bounded ordered
pages, then a seal. Native entries describe the complete adopted choices for each
changed rule; custom entries are admitted option keys. A `None` value advances
generation using an existing membership. Actions pass shared contexts/selections
and owner -> `[binding_slot, context_index, selection_index]` calls. Only
acknowledged generations can be used. `reset(epoch)` retires career data and
requires new publication. None of these operations resolve native callables.

`framing.py` bounds each frame to 256 KiB, `protocol.py` defines closed v2 shapes
and policy normalization, and `action_transfer.py` handles larger logical actions
without repeating contexts or owner tables. These fixed sources and `channel.py`
also bound/assemble multi-owner result fragments; legal Unicode refusals cannot
be forced into one frame. The action's deadline covers both directions. Sources
are also loaded into the native bridge's flat bootstrap namespace, so
their helper names are prefixed and package imports are conditional. This adds
no public mod method, native target selection or generic callable access. Native
validation mappings and dispatch selection are a separate trusted integration.

## Native support and validation contexts

`personnel_adapter.py`, `academy_adapter.py` and `project_adapter.py` compose the sibling
[driver](../drivers/README.md), [staff](../staff/README.md), shared
[personnel](../personnel/README.md), [academy](../academy/README.md) and
[project](../projects/README.md) support packages.
Native planners return owned `clock`, `effective` and `proposal` data without
regulation metadata. `contexts.py` adds schema 1, supported scope, operation and
initiator for the existing validator protocol. Payload fields and wire format are
unchanged; these private internal target names are not mod declarations.

The adapters' `create(state, channel, select, report)` factories skip all inspection
without subscribers. They construct strict native planners and verify completed
imports plus caller/helper signatures before any assignment. Matching signatures
accept changed method bodies without implementation-hash checks. Incompatibility is
reported through the trusted CLI/file reporter and skips the affected integration.
Driver and staff compatibility are checked separately; a usable kind can remain
active when the other kind fails. Shared caller failure affects both kinds.
A partial installation is restored. An active refusal or unavailable validator
retains guards and blocks the attempted native action.

Driver and staff signing run only after all decisions allow, before native early
loan removal or staff signing's prestige/contract/morale writes. One personnel
adapter owns their shared offer and confirmation wrappers.
Caller handling retains pending player confirmations on refusal and keeps native
failure/decline fallback behavior. Project guards keep AI preparation private.
Academy guards preserve native `(success, reason)` results, isolate AI academy/RNG
preparation and retain player refusal notices without closing successful-only UI.
Native preparation side effects are handled by the sibling support packages;
rule subscription, adopted selection, context assembly and dispatch belong here.
The native-support packages do not import this integration.

The fixed bridge source list includes only transport at its new paths. Personnel/
project guard startup wiring, complete market/worker transaction
coverage and public capability admission retain their later gates. JSON ingest
stays under `launcher/regulations`; mod handlers remain in the Wasm runtime.
There are no imports or forwarding aliases at the unpublished former
`hook_manager.regulations` path.
