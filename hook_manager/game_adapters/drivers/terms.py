# SPDX-License-Identifier: MPL-2.0
"""Prepare native driver loan returns on private contract records."""


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
