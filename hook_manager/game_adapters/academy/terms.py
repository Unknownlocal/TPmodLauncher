# SPDX-License-Identifier: MPL-2.0
"""Describe seasonal academy terms using the native season calendar."""

import math
from types import SimpleNamespace

from ...manager import HookError


def academy_terms(contract, season, season_start):
    if contract is None:
        return None
    if type(contract) is not dict:
        raise HookError("Academy planning needs ordinary membership records")
    # Native renewal/remaining-term queries default old records to this season
    # and clamp their full term to at least one season. Never report only the
    # remaining term or the requested extension as the resulting full contract.
    start = int(contract.get("start_season", season))
    term = max(1, int(contract.get("term_seasons", 1)))
    # This is the native yearly billing field. Legacy retainer_m can be used
    # for release penalties, so do not invent an annual bill from that fallback.
    annual = contract.get("annual_cost_m")
    if annual is not None and (type(annual) not in (int, float) or not math.isfinite(annual)):
        raise HookError("Academy retainer needs finite native numbers")
    # Membership expires at a season boundary. Ask the native calendar for both
    # boundaries on tiny private clocks rather than assuming a 52-week term.
    first = season_start(SimpleNamespace(season_no=start))
    end = season_start(SimpleNamespace(season_no=start + term))
    if type(first) is not int or type(end) is not int or end <= first:
        raise HookError("Academy proposal needs native season boundaries")
    return {"role": None, "status": None, "start_week": first, "end_week": end,
            "duration_weeks": end - first, "duration_seasons": term,
            "annual_salary_m": None, "annual_retainer_m": annual,
            "bonuses_m": {"point": None, "win": None, "podium": None}}
