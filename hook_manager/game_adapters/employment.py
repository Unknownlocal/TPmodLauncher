# SPDX-License-Identifier: MPL-2.0
"""Common employment-record fields used by native driver and staff proposals."""

import math

from ..manager import HookError


BONUS_KEYS = ("bonus_per_point_m", "bonus_per_win_m", "bonus_per_podium_m")


def contract_terms(contract, *, role=None, status=None):
    if contract is not None and type(contract) is not dict:
        raise HookError("Employment planning needs ordinary contract records")
    if not contract or not contract.get("team"):
        return None
    # Driver records carry their role; staff records keep it on the person.
    # The staff planner supplies the native normalized role explicitly.
    role = contract.get("role") if role is None else role
    start, duration = contract.get("start_week"), contract.get("length_weeks")
    if any(value is not None and type(value) is not int for value in (start, duration)):
        raise HookError("Employment contract dates need native integer weeks")
    for value in (role, status if status is not None else contract.get("status")):
        if value is not None and type(value) is not str:
            raise HookError("Employment contract role/status need native strings")
    for value in (contract.get("salary_m"), *(contract.get(key) for key in BONUS_KEYS)):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise HookError("Employment contract money needs finite native numbers")
    # Native records store weeks and annual salary, not a separate season count
    # or retainer. Keep absent fields null and use the native exclusive end week.
    return {
        "role": role, "status": status if status is not None else contract.get("status"),
        "start_week": start, "end_week": start + duration if start is not None and duration is not None else None,
        "duration_weeks": duration, "duration_seasons": None,
        "annual_salary_m": contract.get("salary_m"), "annual_retainer_m": None,
        "bonuses_m": dict(zip(("point", "win", "podium"), (contract.get(key) for key in BONUS_KEYS))),
    }


def person_identity(person, kind):
    # Keep the person's own fields separate from normalized employment terms.
    # The reference identifies this live object, not a persistent save-file ID.
    result = {"kind": kind, "ref": kind + ":" + str(id(person)), "name": person.name,
              "nationality": getattr(person, "nationality", None), "age": getattr(person, "age", None),
              "role": getattr(person, "role", None)}
    if (type(result["name"]) is not str or not result["name"]
            or any(result[key] is not None and type(result[key]) is not str for key in ("nationality", "role"))
            or result["age"] is not None and type(result["age"]) is not int):
        raise HookError("Person identity fields do not match the employment proposal")
    return result
