# Regulation validation performance evidence

Measured on 19 October 2026 on Windows 11 build 26200, AMD64 Family 25 Model 97,
8 logical processors. Controller: CPython 3.15.0; owned native client: embedded
CPython 3.13.16; guest: componentizer's CPython 3.14. Wasmtime retains the existing
`none` compiler optimization setting. These are owned-process measurements, not
live game or cross-platform acceptance.

The pre-resident baseline is commit `85a7c98`; the resident candidate starts at
`d3b4c8b` plus this pass's changes. Each runs its own checkout and byte-matching
runner. Private ABI stays 0.4.0. No invocation bound, handler allowance, Store
memory cap or original 100 ms whole-action deadline was increased.

## Warm actions

Median milliseconds across seven clean actions, including native framing,
controller preparation, guest work and native decision validation. Diagnostic
instrumentation runs separately. Every returned decision is checked. Small
one-owner differences are within ordinary scheduling noise; no statistical
confidence claim follows from seven samples.

| Workload | Pre-resident | Candidate |
| --- | ---: | ---: |
| Nationality, 4 calls | 1.32 | 1.28 |
| Salary denial, 4 calls | 1.36 | 1.42 |
| 4 owners, 8 validators/calls each | 8.06 | 5.57 |
| 32 owners sharing one native base | 23.97 | 21.09 |
| 4 owners with custom private rules | 4.98 | 3.96 |
| 16 distinct contexts, 1,024 nodes/invocation | 33.39 | 30.12 |
| 32 distinct contexts, 1,024 nodes/invocation | 64.44 | 61.27 |
| 32 distinct contexts, 4,096 bytes/invocation | 10.96 | 8.09 |
| 32 contexts containing a 3,001-digit integer | 55.71 | 23.37 |
| 32 calls copying a node-heavy stable rule | 50.86 | 16.66 |
| 32 rules x 32 choices, 32 calls | 22.45 | 9.84 |
| 32 rules x 10 node-heavy choices, 32 calls | 61.60 | 19.60 |

All twelve workloads passed seven warm actions; the resident path also passed
metadata-only update, changed payload publication, reset, reload and another
action for each. Source/definition-heavy catalog cases each admit exact 1 MiB
source and exact 1 MiB regulations.json. They exercise accepted large catalogs,
not an exhaustive maximum of every possible accepted shape.

The 32-owner, 256-binding, 1,024-distinct-context/1,024-call stress case exceeded
the original shared deadline on **both** implementations: the native caller
observed unavailability at approximately 102 ms, including shutdown/error return.
It is not a passing throughput result. Transport capacity and per-invocation
limits do not promise completion of every legal combination in 100 ms. Further
throughput optimization remains open for that extreme workload; there is no
fallback to old generations or a restarted deadline.

## Where the earlier regression came from

A separate run of committed `d3b4c8b` with its matching runner measured the
32-context node workload at about 95 ms and eventually timed out. This pass's
single-walk object measurement and empty-container copy paths restore headroom
without reducing input size. The candidate's seven samples were 58.73 to 64.79 ms.
Dynamic data still has to be checked and copied; residency removes stable data
repetition rather than all validation work.

A second repeatable problem appeared with the dense catalog: its first warm
action passed, then automatic cyclic GC scanned the retained mutable payload
graph and exhausted a later export's fuel. Canonical guest values now use flat
scalar-only tuples. A counted generation-0 collection at policy seal untracks
new tuples; handlers receive fresh ordinary dict/list trees reconstructed without
JSON. A full collection was prototyped and rejected: clearing free lists left by
byte/integer actions could exhaust a subsequent small installation's budget.
Generation-0 collection passed those same action/replacement sequences. Author
GC remains enabled, and no author objects are frozen.

Compiler `speed` was also tried locally, but was not adopted: the focused gain
did not justify broadening this change to compilation policy. Matching artifacts
are built once per frozen-source candidate, not once per workload.

## Transfer, sharing and fuel

Diagnostic counts for one warm action:

| Workload | Native request bytes, old -> new | Guest argument bytes, old -> new | Total export fuel, old -> new |
| --- | ---: | ---: | ---: |
| `shared32` | 24,471 -> 4,793 | 19,744 -> 15,840 | 31,601,728 -> 50,866,848 |
| `rule32` | 111,479 -> 954 | 28,016 -> 743 | 308,708,782 -> 158,386,330 |
| `context32` | 115,410 -> 104,338 | 115,232 -> 104,127 | 391,340,455 -> 468,448,481 |

Every successful resident warm diagnostic action had zero stable-payload
encodes, decodes and guest deliveries. Context encodings follow distinct shared
contexts: one for shared32/rule32, 32 for context32. Each subscribed Store still
receives its own context data. Resident references, fences and per-export
crossings add overhead: framework fuel is higher in the small/shared and dynamic
context cases despite lower measured wall time. No allowance was raised to hide
that cost. Per-handler fuel remains 5M with 64 host calls; framework preparation
and installation have their existing separate counted formulas.

For shared32 installation, the host encoded **one native base plus 32 private
overlays**, while 32 Stores each received one records page and sealed their own
view. For catalog/dense_catalog, base encoding counts were 1,024/320 respectively,
matching distinct records, not subscriber count. The combined host map therefore
shares preparation; sandbox isolation deliberately preserves per-Store installs.
Metadata-only updates encoded no catalog payloads and installed no records.

Process CPU totals are recorded independently of wall time. For seven rule32
actions the measured native+controller CPU sum fell from 359.38 to 125.00 ms;
context32 was 437.50 ms in both. Windows reports coarse clock quanta, including
zero for very short actions; those samples cannot support microsecond CPU claims.
Fuel counters include framework plus handlers and are not CPU-time estimates.

## Cold/control costs and retained memory

Clean control timings (one sample per operation, milliseconds):

| Workload | Initial policy | Metadata | Changed payloads | Reset | Reload |
| --- | ---: | ---: | ---: | ---: | ---: |
| `nationality` | 2.89 | 1.19 | 2.15 | 0.65 | 1.94 |
| `shared32` | 52.08 | 23.16 | 36.83 | 9.13 | 34.34 |
| `rule32` | 6.25 | 1.28 | 5.44 | 0.64 | 5.51 |
| `catalog` | 1176.23 | 3.15 | 1245.11 | 7.67 | 1164.91 |
| `dense_catalog` | 1216.37 | 2.22 | 1194.13 | 5.64 | 1149.84 |

These costs are outside action latency. The largest tested catalogs install in
about 1.2 seconds within the unchanged counted installation allowance and shared
five-second per-Store budget. Initial host/native normalization and guest
verification still encode/decode stable data; those are publication costs.

Object-graph observations, deduplicated within each measured root:

| Workload | Host catalog before -> after reset | Guest policy graph before -> after reset | Clean process lifetime peak |
| --- | ---: | ---: | ---: |
| `rule32` | 149,792 -> 73,453 B | 9,804 -> 197 B | 167.3 MiB |
| `catalog` | 43,835,641 -> 21,062,835 B | 3,176,953 -> 197 B | 224.8 MiB |
| `dense_catalog` | 45,644,948 -> 22,234,751 B | 2,755,273 -> 197 B | 219.3 MiB |
| `shared32` | 138,731 -> 55,458 B | 67,696 -> 6,304 B | 1970.1 MiB |

The host catalog root borrows admitted definitions, so reset intentionally retains
those definitions while releasing dynamic policy/views. Each empty guest graph
reports 197 bytes of inspection-root bookkeeping after reset. Retained author
arguments are ordinary author-owned allocations and remain subject to Store
limits. Copying up to four independent invocation pairs is still necessary;
canonical data is never passed to handlers.

Cold admission/bind normally took about 100 ms for one Store, 340 to 352 ms for the
large one-Store source fixtures, and 3.1 seconds for 32 Stores with cached native
compilation. The candidate's first case included a cold compilation: 1.35 seconds
and a 502 MiB process lifetime peak; it is not a warm-Store memory comparison.
The 32-Store process peaks around 1.9 GiB on both designs, reflecting separate
interpreters/native runtime overhead rather than shared rule storage alone.

Host tracemalloc peak during initial publication was approximately 44/45 MiB for
catalog/dense_catalog; process diagnostic peaks were approximately 273/278 MiB.
The latter include instrumentation and offline guest inspection. Clean peaks
above exclude that inspection. RSS includes native code, all interpreters and
allocator retention; it is not exclusive guest heap usage and need not fall at
reset. No claim of a measured instantaneous staging-only Wasm heap peak is made.
The existing 64 MiB per-Store memory cap remains enforced.

## Reproduction and acceptance scope

Use [the harness instructions](README.md). Raw local
reports are under `.state/regulations-resident-pass5/{legacy,final,before}`;
matching bundles are `runner-legacy`, `runner-v5` and `runner-current` respectively.
Legacy component SHA256:
`1e3f055862f2ab0dbe2a3b06f236d8425513a933ebfcaef1c3039c19b9cbecf4`.
Candidate component SHA256:
`c320ac408c164472473618f5901c6531a454bfbbe1ba041943567b1bbf36ae6e`.
Reports include loaded host-source hashes, component hashes, Python versions,
per-operation CPU/wall/fuel/counts/memory and unavailable outcomes. Preserve those
reports when comparing another machine or revision; absolute timings vary.

This establishes removal of repeated stable serialization and owned Windows
workload evidence, including the unresolved extreme throughput limit. Existing
CI cases cover accounting, independent copies, dense-catalog stability and reset;
there is no new benchmark CI job or duplicated test matrix. Actual game function
mapping, live game acceptance, macOS/Linux measurements and public 0.4.0 source
admission remain separate work.
