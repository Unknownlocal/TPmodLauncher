# SPDX-License-Identifier: MPL-2.0
"""Private query reuse must retain native dates, isolation and fresh results."""

import inspect
from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.drivers.queries import DriverQueries
from hook_manager.game_adapters.personnel.callers import PersonnelCallers
from hook_manager.game_adapters.regulations.personnel_adapter import PersonnelAdapter


def test_getter_fast_path_skips_binding_but_preparation_and_other_careers_keep_their_paths():
    state = SimpleNamespace(prestige_manager=object())
    local = SimpleNamespace(personnel_caller=None, committing_personnel=False)
    caller = PersonnelCallers(SimpleNamespace(closed=False, owners={"rules"}, _local=local), state)
    manager = SimpleNamespace(state=state, normalize_contract_status=None)
    driver = SimpleNamespace(contract={"salary": 2})

    def active(self, driver, week=None):
        return driver.contract

    def no_bind(*args, **kwargs):
        pytest.fail("Pass-through getters must not bind arguments")

    wrapped = caller.wrap("active", active, SimpleNamespace(bind=no_bind))
    assert wrapped(manager, driver, week=3) is driver.contract
    local.personnel_caller, local.committing_personnel = {"market": None}, True
    assert wrapped(self=manager, driver=driver) is driver.contract
    with pytest.raises(TypeError):
        wrapped(manager, driver, unknown=True)
    with pytest.raises(TypeError):
        wrapped(manager)
    with pytest.raises(TypeError):
        wrapped(manager, driver, driver=driver)
    with pytest.raises(TypeError):
        wrapped(manager, driver, 1, 2)

    local.committing_personnel = False
    # The direct wrapper must avoid inspect binding during preparation too.
    result = wrapped(manager, driver)
    assert result == driver.contract and result is not driver.contract
    other = SimpleNamespace(state=object())
    assert wrapped(other, driver) is driver.contract


def test_active_inputs_reuse_unexposed_copies_but_recheck_weeks_and_live_changes():
    query, frame = DriverQueries(), {}
    manager = SimpleNamespace(state=object(), normalize_contract_status=None)
    driver = SimpleNamespace(contract=None)
    observed = []

    def active(self, driver, week=None):
        assert self._ai_signing_query_cache is None
        observed.append((self, driver, week))
        if driver.contract is None:
            return None
        driver.contract["normalised"] = True
        if driver.contract["start"] <= week < driver.contract["start"] + driver.contract["length"]:
            return driver.contract

    signature = inspect.signature(active)
    def run(week):
        return query.active(active, manager, driver, week, frame)

    assert run(8) is None and run(9) is None
    other_empty = SimpleNamespace(contract=None)
    assert query.active(active, manager, other_empty, 9, frame) is None
    assert observed[0][1] is observed[2][1] and not frame["query_inputs"].drivers
    assert driver.contract is other_empty.contract is None
    assert observed[0][0] is observed[1][0] and observed[0][1] is observed[1][1]
    driver.contract = {"team": "Alpine", "start": 10, "length": 2, "terms": {"salary": 2}}
    observed.clear()
    assert run(8) is None and run(9) is None
    assert observed[0][1] is observed[1][1]
    assert "normalised" not in driver.contract
    first, second = run(10), run(11)
    assert first == second and first is not second
    first["terms"]["salary"] = 99
    assert run(10)["terms"]["salary"] == driver.contract["terms"]["salary"] == 2
    assert run(12) is None

    # A nested live edit must invalidate a cached None-query input too.
    driver.contract["terms"]["salary"] = 3
    assert run(9) is None
    assert observed[-1][1] is not observed[-2][1]
    assert run(10)["terms"]["salary"] == 3
    driver.contract = {**driver.contract, "start": 20}
    assert run(10) is None and run(20)["start"] == 20
    assert [row[2] for row in observed] == [8, 9, 10, 11, 10, 12, 9, 10, 10, 20]

    def failing(self, driver, week=None):
        driver.contract["terms"]["salary"] = -1
        raise ValueError("Native query failed")

    assert run(19) is None
    with pytest.raises(ValueError, match="Native query failed"):
        query.run("active", failing, signature.bind(manager, driver, 19), frame)
    assert not frame["query_inputs"].managers and not frame["query_inputs"].drivers
    assert run(20)["terms"]["salary"] == 3


def test_signing_clears_shared_query_inputs_even_when_native_writes_then_raises():
    local = SimpleNamespace(personnel_caller={"validation": {}, "normalizations": {}}, committing_personnel=False)
    guards = SimpleNamespace(_local=local)
    query, frame = DriverQueries(), local.personnel_caller
    manager = SimpleNamespace(state=object(), normalize_contract_status=None)
    driver = SimpleNamespace(contract={"start": 10})
    observed = []

    def active(self, driver, week=None):
        observed.append(driver)
        return None

    signature = inspect.signature(active)
    def read():
        return query.run("active", active, signature.bind(manager, driver, 0), frame)

    read()
    def sign():
        assert local.committing_personnel
        assert not frame["query_inputs"].drivers
        # Even inputs added during a re-entrant native call must expire when
        # signing exits, including a native failure after in-place writes.
        read()
        driver.contract["start"] = 20
        raise ValueError("Native signing failed")

    with pytest.raises(ValueError, match="Native signing failed"):
        PersonnelAdapter._execute(guards, sign, (), {})
    assert not local.committing_personnel and not frame["query_inputs"].drivers
    read()
    assert observed[0] is not observed[1] and observed[1] is not observed[2]
    assert observed[2].contract == {"start": 20}
