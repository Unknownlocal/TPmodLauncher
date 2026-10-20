# SPDX-License-Identifier: MPL-2.0
"""Convert native employment records to the shared personnel context shape."""

import math

from ...manager import HookError


BONUS_KEYS = ("bonus_per_point_m", "bonus_per_win_m", "bonus_per_podium_m")


def contract_terms(contract, *, status=None):
    if not contract or not contract.get("team"):
        return None
    start, duration = contract.get("start_week"), contract.get("length_weeks")
    if any(value is not None and type(value) is not int for value in (start, duration)):
        raise HookError("Driver contract dates need native integer weeks")
    for value in (contract.get("role"), status if status is not None else contract.get("status")):
        if value is not None and type(value) is not str:
            raise HookError("Driver contract role/status need native strings")
    for value in (contract.get("salary_m"), *(contract.get(key) for key in BONUS_KEYS)):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise HookError("Driver contract money needs finite native numbers")
    # Native records store weeks and annual salary, not a separate season count
    # or retainer. Keep absent fields null and use the native exclusive end week.
    return {
        "role": contract.get("role"), "status": status if status is not None else contract.get("status"),
        "start_week": start, "end_week": start + duration if start is not None and duration is not None else None,
        "duration_weeks": duration, "duration_seasons": None,
        "annual_salary_m": contract.get("salary_m"), "annual_retainer_m": None,
        "bonuses_m": dict(zip(("point", "win", "podium"), (contract.get(key) for key in BONUS_KEYS))),
    }


def return_owner_loans(manager, driver, employer, requested, years, next_start):
    """Mirror the start body's loan removal on private records before date queries.

    Signing can clear a returned academy loan before later validation fails.
    Never run that branch on a live driver merely to prepare a proposal.
    """
    next_team = manager.driver_team_next_season(driver)
    if not next_team or requested is not None and not requested <= next_start < requested + 52 * years:
        return
    future = driver.future_contract
    if (future and future.get("team") == next_team and future.get("start_week") == next_start
            and future.get("academy_loan") and future.get("loan_owner") == employer):
        driver.future_contract = None
        # Native signing removes only one matching loan: a future loan takes
        # precedence over checking a current loan that overlaps next season.
        return
    current = driver.contract or {}
    if current.get("team") != next_team or not current.get("academy_loan") or current.get("loan_owner") != employer:
        return
    try:
        start, duration = int(current.get("start_week", 0) or 0), int(current.get("length_weeks", 0) or 0)
    except Exception:
        return
    if duration > 0 and start <= next_start < start + duration:
        driver.contract = None
        if driver.team == next_team:
            driver.team = None
