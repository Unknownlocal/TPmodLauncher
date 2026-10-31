# SPDX-License-Identifier: MPL-2.0
"""An approach refusal must reach native callers before negotiation writes."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
import threading
from types import SimpleNamespace

from tests.hook_manager.approach_support import approach_setup as setup


def test_approach_refusal_and_adopted_allow_precede_native_records_and_mail(setup):
    manager, person, guard, channel, seen, cls = setup
    escaped = manager.begin_approach
    values = {"context": {"role": "main", "contract_start_week": "53"}}
    assert escaped(" Driver ", person, " Audi ", **values) == (False, "Nationality must match Audi (german).", None)
    assert manager.records == manager.mail == []
    assert escaped("staff", person, "Audi", context={"contract_start_week": "bad"}) == (
        False, "Unable to determine the intended contract season.", None)
    assert len(seen) == 1 and guard.owners == {"nationality": "ready"}
    assert seen[-1]["effective"] == {"season": 2, "start_week": 53, "end_week": None}
    assert "proposed" not in seen[-1]["proposal"]  # Approach has no invented salary/duration.
    person.nationality = "german"
    assert escaped("driver", person, "Audi", **values)[0]
    person.nationality, channel.allow = "australian", True
    assert escaped("staff", person, "Audi", context={"role": "engineer"})[0]
    assert len(manager.records) == len(manager.mail) == 2
    guard.stop()
    assert escaped("driver", person, "Audi", **values)[0]  # Cached wrapper restores native execution.


def test_worker_approach_unavailable_and_foreign_careers_keep_native_ownership(setup):
    manager, person, guard, channel, seen, cls = setup
    guard.personnel = SimpleNamespace(_local=threading.local())
    def approach_on_worker():
        # The existing AI caller owns a copied negotiation manager/state.
        # Its class hook must validate rather than mistaking it for another career.
        draft = copy(manager)
        draft.state = copy(manager.state)
        guard.personnel._local.personnel_caller = {"market": SimpleNamespace(manager=draft)}
        return draft.begin_approach("staff", person, "Audi", initiated_by="ai")
    with ThreadPoolExecutor(max_workers=1) as worker:
        result = worker.submit(approach_on_worker).result()
    assert result[0] is False and manager.records == manager.mail == []
    assert seen[-1]["initiator"] == "ai"
    channel.validation_ready = False
    assert manager.begin_approach("staff", person, "Audi") == (False, "Validation unavailable", None)
    assert len(seen) == 1 and manager.records == []
    foreign = cls()
    foreign.state, foreign.records, foreign.mail = object(), [], []
    assert foreign.begin_approach("driver", person, "Audi")[0]
    assert foreign.records and manager.records == []
