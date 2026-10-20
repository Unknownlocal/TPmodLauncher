# SPDX-License-Identifier: MPL-2.0
"""Hold native lazy-initialization writes until a signing attempt is allowed."""

from copy import deepcopy
from types import SimpleNamespace


# Scoring can fill driver.personality/career_stage; the prestige getter fills
# team prestige/history. Track those small records, never a whole career copy.
PRESTIGE_FIELDS = ("heritage_prestige", "displayed_prestige", "prestige", "recent_perf_history", "form_prestige")
_PERSON_FIELDS = ("name", "age", "career_stage", "personalities", "personality")


def preparation_draft(frame, live, fields):
    pending = frame["normalizations"]
    # Keep original references until publication, so identities cannot be reused
    # and two getters for one person/team share the same temporary values.
    key = id(live)
    if key not in pending:
        if type(live) is dict:
            # The checked prestige getter builds a new history list and replaces
            # root fields; it never edits the old list or other borrowed values.
            private = dict(live)
        else:
            private = SimpleNamespace(**{name: deepcopy(getattr(live, name))
                                         for name in _PERSON_FIELDS if hasattr(live, name)})
        pending[key] = live, private, set()
    original, private, changed = pending[key]
    changed.update(fields)
    return private


def publish_preparation(frame):
    # The caller supplies the admission boundary. Copy back only the fields the
    # checked helpers can fill, then forget the drafts before native signing.
    pending = frame["normalizations"]
    for live, private, fields in pending.values():
        for field in fields:
            if type(live) is dict:
                if field in private:
                    live[field] = private[field]
            elif hasattr(private, field):
                setattr(live, field, getattr(private, field))
    pending.clear()
