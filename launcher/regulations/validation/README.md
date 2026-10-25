# Regulation validation

This private package prepares rule data and runs mod validators. Rule data stays
in Wasm memory between checks, while each call receives fresh arguments.
`RegulationRuntime` connects the private v2 transport to this package without
adding mod-facing methods or imports.

| File | Owns |
| --- | --- |
| `catalog.py` | Shared native rule values, per-mod data and complete career membership |
| `record.py` | One rule's JSON text and measured size |
| `view.py` | One mod's record, option and binding lookup tables |
| `install.py` | Bounded transfers and the setup/commit budgets |
| `action.py` | Encode proposals once and check per-mod call packets |
| `group.py` | One install transaction, checked mod calls, shared proposals and reset |
| `dispatch.py` | Install career data and run checks in order on the controller thread |

`ValidationDispatch(admitted_definitions, bound_handler)` uses the pack's one
shared `SharedHandlerRunner`. Its binding table must contain exactly the owners
with validators. Definition-only packs pass no handler. The runner binds once
and belongs to one dispatcher, preventing competing record caches or action IDs.
`ValidationGroup` owns the shared install and action state. One manifest describes
all mods and one table describes the physical records. Each mod's view contains
only its bindings, rules and choice references. All views use the career's one
generation. Author code stays inside Wasm.

`publish(native={rule: [effective_option, ...]}, custom={rule: [option_key, ...]})`
installs all subscribed rules once per career. Include every distinct value that
any supported season can select, including saved replacements and future choices.
The controller does not select the current/upcoming pair. Partial installs,
`None` membership and repeat publication are rejected. Another career requires
reset and a complete fresh install. Ordinary adoption selects resident choices
without another transfer.

Native effective objects may be fallbacks, saved replacements or synthesised
options. Their flat operational fields and explicit parameters form the base;
identity/presentation fields are excluded and flat/nested collisions fail. Custom
keys resolve only to the owner's admitted parameters. Overlays always come from
the admitted owner; an unknown synthesised native key gets an empty overlay.
Native values are encoded once; already checked private data is borrowed without
modification. Equal native JSON records share record IDs. New record
IDs never repeat, including after reset or an aborted publication.

The complete manifest and every record must arrive and pass validation before
one commit activates the catalogue. Its acknowledgement covers every mod's view;
a missing owner, record or mismatched reply makes the runner unavailable.

Installation keeps the eight-million base fuel allowance for each owner and
counts shared records once. Transfer and commit share five seconds per owner in
total, within the original `1 + 5 * validator_owners` deadline. Commit reserves its
own portion of that fuel. These are watchdog ceilings, not latency targets.
Reset counts the cached data and indexes it releases, outside handler budgets.

`evaluate(contexts, selections, calls, deadline=...)` takes shared action tables.
Each selection has `rule`, `evaluation_season`, `selected`, `current`, `upcoming`.
References are `[choice_index_within_that_rule, effective_season]`; upcoming may
be null. Calls map owner IDs to ordered `[validator_id, context_index,
selection_index]` rows. Indexes refer to the distinct membership order published
above. The caller owns native season/selection semantics; this layer checks
authority, order, freshness and full logical limits. Every owner packet is
prepared before author execution, with no stable payload encoding or traversal.
Contexts are encoded and measured once, then sent as one shared table. Each mod's
packet refers to numbered entries. Full limits are checked before replacing
contexts with references. Handlers receive independent argument copies and run
in owner-ID order. The shared action must finish before any results are returned.

Preparing up to four calls has a 64-million fuel allowance. Each handler keeps
its five-million fuel and 64 host-call limits. All owners share the original
100 ms action deadline, including argument preparation and cleanup.
Failure makes the whole source group unavailable, including peers omitted from
the action or install. Earlier approvals cannot become a partial success.
`reset(new_epoch)` clears career data while retaining bindings and increasing
action/record IDs. It runs once for the shared runner, requires fresh publication
before use, and cannot revive a failed runner.

Native transport and pack readiness use this package through the runtime.
`PackMods` supplies its shared source group through `RegulationRuntime`.
The published limits on packet size and item count do not guarantee arbitrary
workloads finish within 100 ms.
