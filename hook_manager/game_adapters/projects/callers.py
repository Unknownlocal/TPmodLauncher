# SPDX-License-Identifier: MPL-2.0
"""Keep checked AI preparation from committing project data before validation.

The native AI asks helpers that initialize stores/part history and sets its
chassis package before start. These wrappers give those reads private containers.
An allowed native start runs against the real state and stores its own package.
"""

from copy import deepcopy


class ProjectCallers:
    def __init__(self, guards, state):
        self.guards, self.state = guards, state

    def wrap(self, role, original, signature):
        def wrapped(*args, **kwargs):
            guards = self.guards
            if guards.closed or not guards.owners:
                return original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            if getattr(values["self"], "state", None) is not self.state:
                return original(*args, **kwargs)
            caller = getattr(guards._local, "project_caller", None)
            if role in ("aero_ui", "chassis_ui", "ai_current", "ai_future", "ai_aero", "aero_ai"):
                return self._caller(role, original, bound, caller)
            if caller is None or caller["initiator"] != "ai" or getattr(guards._local, "committing_project", False):
                # Draft reads are only for AI preparation. An allowed native
                # start must initialize and edit its real stores/part records.
                return original(*args, **kwargs)
            if role == "package" and caller["role"] == "ai_current":
                # This checked caller discards the return and passes the package
                # explicitly to start_project, which writes it only on success.
                return True
            if role in ("aero_store", "chassis_store", "ai_project"):
                section = "aero" if role == "aero_store" else "development"
                values["team"] = {section: {"projects": dict(values["team"].get(section, {}).get("projects", {}))}}
            elif role == "aero_parts":
                team, part = values["team"], values["part_key"]
                key = id(team), part
                if key not in caller["parts"]:
                    record = team.get("aero", {}).get("parts", {}).get(part, {})
                    caller["parts"][key] = {"aero": {"parts": {part: deepcopy(record)}}}
                values["team"] = caller["parts"][key]
            return original(*bound.args, **bound.kwargs)
        return wrapped

    def _caller(self, role, original, bound, previous):
        guards = self.guards
        caller = {"role": role, "initiator": "player" if role.endswith("_ui") else "ai", "parts": {}}
        # Native checks can return before start_project reaches a guard. That
        # means no validation outcome for this call, regardless of the last call.
        guards._local.outcome = None
        guards._local.project_caller = caller
        # Treat preparation and the nested start as one operation for registry
        # changes/shutdown. The nested guard still checks scoped worker access.
        with guards._lock:
            guards._active += 1
        try:
            if role == "aero_ai":
                live = bound.arguments["ai_state"]
                draft = dict(live)
                for key in ("pool_slack", "pool_commitments"):
                    if key in draft:
                        draft[key] = dict(draft[key])
                bound.arguments["ai_state"] = draft
                result = original(*bound.args, **bound.kwargs)
                if result[0]:
                    # Only this checked caller's four output fields are committed.
                    # No write-back occurs after a refused start.
                    for key in ("available_after_reserve", "cash_after_reserve", "pool_slack", "pool_commitments"):
                        if key in draft:
                            live[key] = draft[key]
                return result
            return original(*bound.args, **bound.kwargs)
        finally:
            guards._local.project_caller = previous
            with guards._lock:
                guards._active -= 1
