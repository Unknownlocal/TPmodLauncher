# SPDX-License-Identifier: MPL-2.0
"""Let native callers prepare market records and mail before publishing their work."""

from copy import copy
from contextlib import nullcontext

from ...callables import method_group
from ...manager import HookError
from ..native_methods import native_signatures
from .native_methods import EMAIL
from .market_graph import MarketGraph
from .market_mail import MarketMail


class MarketPreparation:
    """One private native caller view, with explicit checkpoints for completed work."""

    _STORES = ("records", "candidate_deadlines", "driver_proposals", "pending_player_decisions")
    _COUNTERS = ("_serial", "_proposal_serial")

    @classmethod
    def check(cls, state):
        """Check the native storage and writer signatures before installing hooks."""
        manager = state.personnel_negotiation_manager
        if (any(type(getattr(manager, name, None)) is not dict for name in cls._STORES)
                or any(type(getattr(manager, name, None)) is not int for name in cls._COUNTERS)):
            raise HookError("Personnel market stores/counters are unavailable")
        mail = getattr(state, "email_manager", None)
        if mail is not None:
            if (mail.state is not state or type(mail.inbox) is not list
                    or type(mail.urgent_sound_serial) is not int):
                raise HookError("Personnel mailbox storage is unavailable")
            method_group(mail, native_signatures(EMAIL, "send_email"))
        relationships = getattr(state, "relationship_manager", None)
        if callable(getattr(relationships, "adjust_market_team_relationship", None)):
            method_group(relationships, {"adjust_market_team_relationship": "(self, driver_name: 'str', team_name: 'str', amount: 'int', reason: 'str' = '') -> 'int'"})

    def __init__(self, state):
        self.live = state.personnel_negotiation_manager
        # Borrow the rest of the career through shallow owner copies. Only the
        # market stores and inbox belong to this view; allowed native signing
        # still acts on real people/teams through the existing signing guards.
        self.state = copy(state)
        self.manager = copy(self.live)
        self.graph = MarketGraph()
        for name in self._STORES:
            setattr(self.manager, name, self.graph.copy(getattr(self.live, name)))
        for name, value in vars(self.live).items():
            # A native manager may keep another reference to a copied store/row.
            # Rebind that alias too, rather than letting it bypass preparation.
            if self.graph.private(value) is not value:
                setattr(self.manager, name, self.graph.private(value))
        self.manager.state = self.state
        self.state.personnel_negotiation_manager = self.manager
        self._relationship_calls = []
        relationships = getattr(state, "relationship_manager", None)
        self._adjust = getattr(relationships, "adjust_market_team_relationship", None)
        if callable(self._adjust):
            # Declining a confirmation can try a backup next. Hold the penalty
            # until that decision finishes, so unavailable backup validation
            # cannot leave a penalty behind on a still-pending confirmation.
            # The native helper ignores this writer's return value.
            private = copy(relationships)
            private.adjust_market_team_relationship = self._queue_relationship
            self.state.relationship_manager = private
        mail = getattr(state, "email_manager", None)
        self.mail = MarketMail(mail, self.state, self.graph) if mail is not None else None
        self._save = getattr(state, "save_season", None)
        if self._save is not None:
            self.state.save_season = self.save

    def bind(self, bound):
        """Route nested native callers into the same working manager and rows."""
        owner = bound.arguments["self"]
        bound.arguments["self"] = self.manager
        if "row" in bound.arguments:
            row = bound.arguments["row"]
            # External callers supply live rows; native callers inside the view
            # can also supply newly created rows, which are already private.
            bound.arguments["row"] = self.graph.copy(row) if owner is self.live else self.graph.private(row)
        for name in ("source_row", "winner", "player_row"):
            if name in bound.arguments:
                bound.arguments[name] = self.graph.private(bound.arguments[name])

    def publish(self):
        """Publish remaining native work on normal completion or before saving."""
        self._publish_relationships()
        values = {name: getattr(self.manager, name) for name in self._STORES}
        if self.mail is not None:
            values["inbox"] = self.mail.private.inbox
        prepared = self.graph.publish(values)
        for name in self._STORES:
            setattr(self.live, name, prepared[name])
        self._publish_counters()
        if self.mail is not None:
            self.mail.live.inbox = prepared["inbox"]
            self.mail.live.urgent_sound_serial = self.mail.private.urgent_sound_serial

    def mail_for(self, row):
        # Offer execution uses a temporary row with the same native record ID.
        # Attribute its mail to the stored row that the checkpoint will select.
        row = self.manager.records.get(row.get("id"), self.graph.private(row))
        return self.mail.for_row(row) if self.mail is not None else nullcontext()

    def checkpoint(self, row=None, *, candidate_scope=False, exclude=()):
        """Publish one offer, a completed candidate group, or earlier terminal rows."""
        if row is not None:
            # Cancellation's terminal-only scan must not flush a pending decline.
            self._publish_relationships()
        keys = {row.get("candidate_key")} if row is not None and candidate_scope else set()
        changes, removed = {}, []
        selected_rows = set()
        # Staff AI can fail a row inline; a filled driver seat can cancel backups
        # for other people. Retain those exact completed rows, without publishing
        # the other offers that those people may still be negotiating.
        for key, candidate in self.manager.records.items():
            if (candidate.get("status") in {"completed", "cancelled", "failed"}
                    and self.live.records.get(key, {}).get("status") not in {"completed", "cancelled", "failed"}
                    and id(candidate) not in exclude):
                selected_rows.add(id(candidate))
                changes["records", key] = candidate
                candidate_key = candidate.get("candidate_key")
                # A failed row alone does not imply deadline cleanup. Only keep
                # removals that native code has actually made in the view.
                if candidate_key not in self.manager.candidate_deadlines:
                    removed.append(("candidate_deadlines", candidate_key))
        for name in self._STORES:
            live, private = getattr(self.live, name), getattr(self.manager, name)
            for key in live.keys() | private.keys():
                value = private.get(key, live.get(key))
                candidate_key = key if name == "candidate_deadlines" else value.get("candidate_key")
                # Closing/staging ends the whole candidate market. An individual
                # AI offer failure publishes only its own record and messages.
                if candidate_key in keys or name == "records" and value is row:
                    if key in private:
                        changes[name, key] = value
                        if name == "records":
                            selected_rows.add(id(value))
                    else:
                        removed.append((name, key))
        if self.mail is not None:
            changes.update(self.mail.changes(selected_rows))
        if not changes and not removed:
            # Do not publish provisional serials for a cancelled new attempt
            # when there was no earlier completed work to retain.
            return
        prepared = self.graph.publish(changes)
        for (name, key), value in prepared.items():
            if name != "mail":
                getattr(self.live, name)[key] = value
        for name, key in removed:
            getattr(self.live, name).pop(key, None)
        self._publish_counters()
        if self.mail is not None:
            self.mail.checkpoint()

    def _publish_counters(self):
        for name in self._COUNTERS:
            setattr(self.live, name, getattr(self.manager, name))

    def _queue_relationship(self, *args, **kwargs):
        self._relationship_calls.append((args, kwargs))

    def _publish_relationships(self):
        pending, self._relationship_calls = self._relationship_calls, []
        for args, kwargs in pending:
            try:
                self._adjust(*args, **kwargs)
            except Exception:
                # The native confirmation helper also ignores adjustment errors.
                pass

    def save(self, *args, **kwargs):
        """Make the real save observe the market state native code just prepared."""
        self.publish()
        return self._save(*args, **kwargs)

    def finish(self, frame):
        """Keep native completion/exception behavior; discard refused preparation."""
        if not frame["refusal"]:
            self.publish()
        elif frame["offer_attempt"]:
            blocked = frame["offer_attempt"][0]
            excluded = {id(blocked)}
            if frame.get("role") == "decision":
                # A player decline and its backup attempts form one decision.
                # AI markets instead retain earlier failures by other teams,
                # even if they were negotiating with this same person.
                excluded.update(id(row) for row in self.manager.records.values()
                                if row.get("candidate_key") == blocked.get("candidate_key"))
            self.checkpoint(exclude=excluded)
            row = self.graph.original(blocked)
            if row is not None:
                # Retain the diagnostic on an existing row only; a cancelled
                # new offer must not create a live record just to show a reason.
                row["execution_block_reason"] = frame["refusal"].message
