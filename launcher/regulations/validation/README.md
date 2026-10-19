# Regulation validation

This private package prepares rule data and coordinates mod validator execution.
It uses the resident guest protocol: rule data stays loaded between validation
calls, while each call receives fresh arguments.
`RegulationRuntime` connects the private v2 transport to this package. It adds no
mod-facing methods or imports; the old full-rule execution path has been removed.

| File | Owns |
| --- | --- |
| `catalog.py` | Combined native bases, owner-private data and reverse rule subscriptions |
| `record.py` | One canonical payload, encoding and exact component measurements |
| `view.py` | An owner's filtered record/choice/binding indexes and cached manifest |
| `install.py` | Finite pages/chunks, exact operation counts and split setup fuel |
| `action.py` | Once-encoded action contexts, owner-local references and expanded bounds |
| `runner.py` | Checked private guest acknowledgements and ordinary handler execution |
| `dispatch.py` | Controller-thread publication, cohort fencing, ordering and owner failure |

Construct `ValidationDispatch(admitted_definitions, bound_handlers)`. The handler map
contains every owner with at least one validator; keys and binding declarations
must match admission. Definition-only owners remain governance concerns. The
definitions provide borrowed internal references, including admitted fallbacks;
public definition views still return independent copies. No author Python is
loaded in the host.

`publish(native={rule: [effective_option, ...]}, custom={rule: [option_key, ...]})`
supplies complete **distinct reachable membership** for each changed rule. Include
every adopted future payload that a supported season can select. The controller
does not infer adoption or select a current/upcoming pair. Initial publication
covers every bound rule. Later updates can name only changed rules; their reverse
subscriptions select affected owners. Use `{rule: None}` for unchanged membership
whose publication generation must advance, avoiding all payload codec work.

Native effective objects may be fallbacks, saved replacements or synthesized
options. Their flat operational fields and explicit parameters form the base;
identity/presentation fields are excluded and flat/nested collisions fail. Custom
keys resolve only to the owner's admitted parameters. Overlays always come from
the admitted owner; an unknown synthesized native key gets an empty overlay.
Canonical native values are copied once; immutable admitted private data is
borrowed. Equal native record encodings can reuse active immutable IDs. New record
IDs never repeat, including after reset or an aborted publication. Unreachable
dynamic records retire after commit.

Every affected Store stages and seals before any commits. A partial commit failure
leaves all affected owners unavailable; no old-generation fallback exists. The
eight-million-base installation formula is split between staging and commit,
reserving retired-graph cleanup for commit. Stage and commit share five seconds
of per-Store processing time; waiting for other Stores consumes the original
transaction deadline of `1 + 5 * affected_owners` seconds. No phase restarts either
allowance. This is a watchdog ceiling, not a latency target.

`evaluate(contexts, selections, calls, deadline=...)` takes shared action tables.
Each selection has `rule`, `evaluation_season`, `selected`, `current`, `upcoming`.
References are `[choice_index_within_that_rule, effective_season]`; upcoming may
be null. Calls map owner IDs to ordered `[validator_id, context_index,
selection_index]` rows. Indexes refer to the distinct membership order published
above. The caller owns native season/selection semantics; this layer checks
authority, order, freshness and full logical limits. Every owner packet is
prepared before author execution, with no stable payload encoding or traversal.
Contexts are encoded/measured once globally, then borrowed as fragments by each
participating Store packet. Guest arguments remain independent ordinary copies.

Entry has counted framework fuel; later four-slot copying retains 64M, handlers
retain 5M/64 host calls, and all owners share the original 100 ms action deadline.
Only the executing failed owner is latched unavailable; a partial publication
failure fences the entire affected cohort. Required owners remain in the model.
`reset(new_epoch)` clears policy views while preserving bindings and monotonically
increasing action/record identities. Reset cannot revive a failed Store.

Native transport and pack readiness use this package through the runtime.
[Owned-process performance measurements](../../../benchmarks/regulations/RESULTS.md)
cover this path; live game mappings remain a separate acceptance gate. Supported
packet cardinality does not guarantee arbitrary workloads finish within 100 ms.
