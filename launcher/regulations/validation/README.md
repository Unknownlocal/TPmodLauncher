# Regulation validation

This private package prepares rule data and runs mod validators. Rule data stays
in Wasm memory between checks, while each call receives fresh arguments.
`RegulationRuntime` connects the private v2 transport to this package without
adding mod-facing methods or imports.

| File | Owns |
| --- | --- |
| `catalog.py` | Shared native rule values, per-mod data and the mods using each rule |
| `record.py` | One rule value, its JSON text and measured size |
| `view.py` | One mod's record, option and binding lookup tables |
| `install.py` | Bounded transfers and the setup/commit budgets |
| `action.py` | Encode proposals once and check per-mod call packets |
| `runner.py` | Checked private guest acknowledgements and ordinary handler execution |
| `group.py` | Shared record reuse, one proposal table, group completion and reset |
| `dispatch.py` | Publish updates and run checks in order on the controller thread |

Construct `ValidationDispatch(admitted_definitions, bound_handlers)`. The handler map
contains every owner with at least one validator; keys and binding declarations
must match admission. Mods defining rules without validators need no execution
helper. The dispatcher uses already checked definitions and fallback rules;
public readers still receive independent copies. No author Python is loaded in
the host.

For individual source runners, each value is its owner's `HandlerRunner`. For
shared source runners, every owner in a group maps to the same
`SharedHandlerRunner`. Its complete binding table must be present. A physical
runner binds once and can belong to only one dispatcher. `ValidationRunner`
retains each owner's generation and view; `ValidationGroup` coordinates the
shared Store without combining the original owner IDs.

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
Native values are copied once; already checked private data is borrowed without
modification. Equal native JSON records can reuse active IDs. New record
IDs never repeat, including after reset or an aborted publication. Unreachable
dynamic records retire after commit.

Every affected owner stages and seals before any commits. A partial commit failure
leaves the affected Stores unavailable; no old-generation fallback exists. The
eight-million-base installation formula is split between staging and commit,
reserving the work of releasing replaced data for commit. Stage and commit share
five seconds of per-owner processing time; waiting for other Stores consumes the original
transaction deadline of `1 + 5 * affected_owners` seconds. No phase restarts either
allowance. This is a watchdog ceiling, not a latency target.

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
Contexts are encoded/measured once globally. Each shared Store receives one table
of the contexts it uses; owner packets carry numbered references. Individual
Stores retain their filtered context packets. Full limits are checked before
replacing contexts with references. Guest arguments remain independent ordinary
copies. Owner execution order is preserved even when shared and individual Stores
are interleaved. A group opens its table at its first requested owner and checks
completion after its last, before any results can leave the dispatcher.

Entry has counted framework fuel; later four-slot copying retains 64M, handlers
retain 5M/64 host calls, and all owners share the original 100 ms action deadline.
Failure makes every owner in the failed physical Store unavailable, including
peers not named in that action or update. Other groups with unfinished action
state also close; earlier approvals cannot become a partial success. Individual
Stores that remain usable retain their status. Required owners stay in the model.
`reset(new_epoch)` clears policy views while preserving bindings and monotonically
increasing action/record identities. Reset runs once per physical group, requires
new publication before use, and cannot revive a failed Store.

Native transport and pack readiness use this package through the runtime.
[Owned-process performance measurements](../../../benchmarks/regulations/RESULTS.md)
cover the individual-runner path. `PackMods` supplies shared source groups through
`RegulationRuntime`; direct trusted callers can also supply individual handlers.
The published limits on packet size and item count do not guarantee arbitrary
workloads finish within 100 ms.
