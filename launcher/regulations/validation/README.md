# Regulation validation

This private package prepares rule data and runs mod validators. Rule data stays
in Wasm memory between checks, while each call receives fresh arguments.
`RegulationRuntime` connects the private v2 transport to this package without
adding mod-facing methods or imports.

| File | Owns |
| --- | --- |
| `catalog.py` | Shared native rule values, per-mod data and the mods using each rule |
| `record.py` | One rule's JSON text and measured size |
| `view.py` | One mod's record, option and binding lookup tables |
| `install.py` | Bounded transfers and the setup/commit budgets |
| `action.py` | Encode proposals once and check per-mod call packets |
| `runner.py` | Checked private guest acknowledgements and ordinary handler execution |
| `group.py` | Shared record reuse, one proposal table, group completion and reset |
| `dispatch.py` | Publish updates and run checks in order on the controller thread |

`ValidationDispatch(admitted_definitions, bound_handler)` uses the pack's one
shared `SharedHandlerRunner`. Its binding table must contain exactly the owners
with validators. Definition-only packs pass no handler. The runner binds once
and belongs to one dispatcher, preventing competing record caches or action IDs.
`ValidationRunner` retains each mod's generation and view; `ValidationGroup`
manages their shared records, proposals and reset. Author code stays inside Wasm.

`publish(native={rule: [effective_option, ...]}, custom={rule: [option_key, ...]})`
supplies all distinct option values that any supported season can select for each
changed rule. Include saved replacements and adopted future values. The controller
does not infer adoption or select a current/upcoming pair. Initial publication
covers every bound rule. Later updates can name only changed rules; the catalogue's
list of mods using each rule selects affected owners. Use `{rule: None}` to retain
the options while advancing their generation, without encoding or decoding rule data.

Native effective objects may be fallbacks, saved replacements or synthesised
options. Their flat operational fields and explicit parameters form the base;
identity/presentation fields are excluded and flat/nested collisions fail. Custom
keys resolve only to the owner's admitted parameters. Overlays always come from
the admitted owner; an unknown synthesised native key gets an empty overlay.
Native values are encoded once; already checked private data is borrowed without
modification. Equal native JSON records can reuse active IDs. New record
IDs never repeat, including after reset or an aborted publication. Unreachable
dynamic records retire after commit.

Every affected owner stages and seals before any commits. A partial commit failure
makes the shared runner unavailable; no old-generation fallback exists. The
eight-million-base installation formula is split between staging and commit,
reserving the work of releasing replaced data for commit. Stage and commit share
five seconds of per-owner processing time. Work for other owners still consumes
the original transaction deadline of `1 + 5 * affected_owners` seconds. No phase
restarts either allowance. This is a watchdog ceiling, not a latency target.

A shared group supplies the union of installed and newly staged record IDs to
installation planning. Another owner can borrow a common native record without
transferring it again. After all commits, one group operation retires unused
records. Its fuel counts the cached payloads released and indexes scanned; its
time comes from the affected owners' remaining control allowances.

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
the action or update. Earlier approvals cannot become a partial success.
`reset(new_epoch)` clears career data while retaining bindings and increasing
action/record IDs. It runs once for the shared runner, requires fresh publication
before use, and cannot revive a failed runner.

Native transport and pack readiness use this package through the runtime.
`PackMods` supplies its shared source group through `RegulationRuntime`.
The published limits on packet size and item count do not guarantee arbitrary
workloads finish within 100 ms.
