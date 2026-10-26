# SPDX-License-Identifier: MPL-2.0
"""Inert policy/packet fixtures for retained-value accounting checks."""

import hashlib

from mod_runtime._regulations.values import measure


def policy(owner, values=None, *, count=1):
    values = values or [{"points": [25, 18]}, {"points": [30, 20]}, {"points": [35, 25]}]
    records = values + [{"owner": owner}]
    descriptors = []
    for slot, value in enumerate(records):
        metric = measure(value)
        descriptors.append(dict(slot=slot, id=slot + 1, kind="parameters" if slot < len(values) else "option_data",
                                **dict(zip(("bytes", "nodes", "depth", "integer_work"), metric))))
    return {"owners": [{"owner": owner, "revision": hashlib.sha256(owner.encode()).hexdigest(),
                        "bindings": [{"rule": 0, "scope": "personnel.contract"}] * count, "rules": ["native"],
                        "choices": [[0, "open", i, len(values)] for i in range(len(values))]}],
            "records": descriptors, "totals": {key: sum(row[key] for row in descriptors) for key in ("bytes", "nodes", "integer_work")}}, records


def packet(manifest, count=1, *, selected=0):
    return {"owner": manifest["owners"][0]["owner"], "revision": manifest["owners"][0]["revision"],
            "contexts": [0],
            "selections": [{"rule": 0, "evaluation_season": 2026, "selected": "current",
                            "current": [selected, 2026], "upcoming": None}], "calls": [[index, 0, 0] for index in range(count)]}
