# SPDX-License-Identifier: MPL-2.0
"""Run pre-signing queries without normalizing live agreements or caches."""

from copy import deepcopy
from types import SimpleNamespace

from ..personnel.preparation import preparation_draft
from .query_inputs import DriverQueryInputs


class DriverQueries:
    def run(self, role, original, bound, frame):
        values = bound.arguments
        manager = values["self"]
        if role == "active":
            if "query_inputs" not in frame:
                frame["query_inputs"] = DriverQueryInputs()
            return frame["query_inputs"].active(original, bound)
        # These getters fill missing identity fields. Keep their answers
        # available to nested queries on the same private person data.
        if role in ("personalities", "career"):
            fields = ("personalities", "personality") if role == "personalities" else ("career_stage",)
            values["driver"] = preparation_draft(frame, values["driver"], fields)
            return original(*bound.args, **bound.kwargs)
        # Status normalization only serves this read. Unlike missing identity/
        # prestige data, it need not be staged for later publication.
        if role == "status":
            values["contract"] = deepcopy(values["contract"])
            return original(*bound.args, **bound.kwargs)
        # These dependencies come from the inspected query bodies. The native
        # functions still calculate the answer; only their writable inputs change.
        private = SimpleNamespace(
            state=manager.state, drivers=manager.drivers, _ai_signing_query_cache=None,
            normalize_contract_status=manager.normalize_contract_status,
            active_contract=manager.active_contract, _contract_covers_week=manager._contract_covers_week,
        )
        values["self"] = private
        if role in ("entries", "next_entries"):
            originals = {}
            private.drivers = []
            for person in manager.drivers:
                clone = SimpleNamespace(contract=deepcopy(person.contract),
                                        future_contract=deepcopy(getattr(person, "future_contract", None)))
                private.drivers.append(clone)
                originals[id(clone)] = person
                if values.get("exclude_driver") is person:
                    values["exclude_driver"] = clone
            result = original(*bound.args, **bound.kwargs)
            # Callers compare driver identity (for example, excluding the subject).
            # Return original people paired with privately normalized agreements.
            return [(originals[id(person)], contract) for person, contract in result]
        return original(*bound.args, **bound.kwargs)
