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
| `runner.py` | Checked private guest acknowledgements and ordinary handler execution |
| `group.py` | Shared record reuse, one proposal table, group completion and reset |
| `dispatch.py` | Install career data and run checks in order on the controller thread |

`ValidationDispatch(admitted_definitions, bound_handler)` uses the pack's one
shared `SharedHandlerRunner`. Its binding table must contain exactly the owners
with validators. Definition-only packs pass no handler. The runner binds once
and belongs to one dispatcher, preventing competing record caches or action IDs.
`ValidationRunner` retains each mod's generation and view; `ValidationGroup`
manages their shared records, proposals and reset. Author code stays inside Wasm.

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

Every validator owner stages and seals before any commits. A partial commit failure
makes the shared runner unavailable; no old-generation fallback exists. The
eight-million-base installation formula is split between staging and commit,
reserving a separate commit crossing allowance. Stage and commit share
five seconds of per-owner processing time. Work for other owners still consumes
the original transaction deadline of `1 + 5 * validator_owners` seconds. No phase
restarts either allowance. This is a watchdog ceiling, not a latency target.

A shared group supplies already staged record IDs to installation planning.
Another owner can borrow a common native record without transferring it again.
After all commits, one operation confirms every owner's complete install.
It uses the owners' remaining control allowance. Reset counts the old cached data
and indexes it releases, outside ordinary handler budgets.

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
