# SPDX-License-Identifier: MPL-2.0
"""Deduplicate bounded guest calls without widening expanded invocation limits."""

from mod_runtime._regulations.policy import MAX_CALLS, encode


def pack(calls):
    contexts, rules, rows = [], [], []
    context_keys, rule_keys = {}, {}
    for index, context, regulation in calls:
        references = []
        for value, values, keys in ((context, contexts, context_keys), (regulation, rules, rule_keys)):
            key = encode(value)
            if key not in keys:
                keys[key] = len(values)
                values.append(value)
            references.append(keys[key])
        rows.append([index, *references])
    return encode({"contexts": contexts, "rules": rules, "calls": rows})


def batches(calls):
    """Yield batches or an individual call when batch headers alone would overflow.

    Calls are already copied/validated by HandlerRunner. Individual exports split
    context and regulation into scalar arguments, so a valid 4-KiB expanded
    envelope remains usable even when a batch header does not fit beside it.
    """
    current = []
    for call in calls:
        candidate = current + [call]
        packet = pack(candidate)
        if len(candidate) <= MAX_CALLS and len(packet.encode("utf-8")) <= 4096:
            current = candidate
            continue
        if current:
            yield pack(current), current
            current = []
        single = pack([call])
        if len(single.encode("utf-8")) <= 4096:
            current = [call]
        else:
            yield None, [call]
    if current:
        yield pack(current), current
