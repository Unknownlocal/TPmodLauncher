# SPDX-License-Identifier: MPL-2.0
"""Read annual payroll from native contracts without changing career records.

The caller supplies its live or private rosters and prepared native terms, before
conversion to the public contract format. Salary rates stay in native millions
per year. Selecting caps or deciding whether a contract is allowed belongs to
the consuming rule, separately from this calculation.
"""

import math

from ...manager import HookError
from ..native_values import native_value


def _week(value):
    if type(value) is not int:
        raise HookError("Payroll dates need native integer weeks")
    return value


def _salary(contract, employer, week):
    if not contract or contract.get("team") != employer:
        return 0.0
    start = _week(contract.get("start_week", 1))
    length = _week(contract.get("length_weeks", 0))
    if not start <= week < start + length:
        return 0.0
    # Weekly wages use zero for a missing salary. Invalid money must instead
    # stop the calculation; a guessed zero would understate the commitment.
    salary = contract.get("salary_m", 0.0)
    if type(salary) not in (int, float) or not math.isfinite(salary):
        raise HookError("Payroll money needs finite native numbers")
    return salary


def payroll_identity(drivers):
    """Remember driver salary inputs across a private staffing batch.

    Staff publication checks its own source roster. Borrowed driver contracts
    need this separate comparison, without copying people or their histories.
    """
    identities = []
    for person in drivers:
        records = (person.contract, getattr(person, "future_contract", None))
        if not any(records):
            continue
        values = tuple(
            tuple(native_value(record.get(key, default)) for key, default in (
                ("team", None), ("start_week", 1), ("length_weeks", 0), ("salary_m", 0.0)))
            if type(record) is dict else native_value(record)
            for record in records)
        identities.append((id(person), values))
    return tuple(identities)


def prepare_payroll(state, employer, candidate, kind, proposed, current_week, next_start, checks):
    """Collect totals and a private freshness check from the same roster view."""
    clock = state.season_no, state.week_index
    def collect():
        return project_payroll(state.driver_manager.drivers, state.staff_manager.staff, employer,
                               candidate, kind, proposed, current_week, next_start)

    totals = collect()

    def unchanged():
        try:
            # Rules receive only these totals. Rechecking them avoids keeping a
            # second roster snapshot while still covering changes during dispatch.
            return clock == (state.season_no, state.week_index) and totals == collect()
        except (AttributeError, TypeError, HookError):
            return False

    checks.append(unchanged)
    return totals


def project_payroll(drivers, staff, employer, candidate, kind, proposed, current_week, next_start):
    """Return proposed staff/driver annual salaries and their absolute game week.

    ``proposed`` uses native salary_m/start_week/length_weeks keys. Observe a
    backdated signing today, or a future signing at its start. Due future deals
    replace current agreements at the next season boundary; their end is exclusive.
    The caller owns the consistent roster view. No roster copies, native queries,
    caches or writes are needed to calculate these known salary commitments.
    """
    if (type(employer) is not str or not employer or kind not in ("driver", "staff")
            or type(proposed) is not dict or proposed.get("team") != employer):
        raise HookError("Payroll needs prepared team employment terms")
    week = max(_week(current_week), _week(proposed.get("start_week")))
    if week > _week(next_start) or next_start < current_week:
        raise HookError("Payroll needs the native current/next-season start range")

    def amounts(roster, group):
        for person in roster:
            if group == kind and person is candidate:
                # Replace the candidate once, including any scheduled deal. A
                # generated candidate can also be absent from the supplied roster.
                continue
            current = person.contract
            future = getattr(person, "future_contract", None)
            if ((current is not None and type(current) is not dict)
                    or (future is not None and type(future) is not dict)):
                raise HookError("Payroll needs ordinary native contract records")
            if not ((current and current.get("team") == employer)
                    or (future and future.get("team") == employer)):
                continue
            # Choose the due agreement before filtering its employer: someone
            # moving away must not leave their superseded salary in this total.
            selected = current
            if week == next_start and future and _week(future.get("start_week")) <= week:
                selected = future
            yield _salary(selected, employer, week)
        if group == kind:
            yield _salary(proposed, employer, week)

    try:
        staff_total = math.fsum(amounts(staff, "staff"))
        driver_total = math.fsum(amounts(drivers, "driver"))
    except OverflowError as error:
        raise HookError("Payroll total exceeds finite native money") from error
    return {"as_of_week": week, "staff_annual_salary_m": staff_total, "driver_annual_salary_m": driver_total}
