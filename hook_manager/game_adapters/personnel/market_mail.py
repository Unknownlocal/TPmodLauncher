# SPDX-License-Identifier: MPL-2.0
"""Hold native personnel mail until its corresponding market record completes."""

from contextlib import contextmanager
from copy import copy


class MarketMail:
    """Keep a working inbox and track which market record sent each new message."""

    def __init__(self, live, state, graph):
        self.live, self.graph = live, graph
        self.private = copy(live)
        self.private.state = state
        self.private.inbox = graph.copy(live.inbox)
        state.email_manager = self.private
        self._row = None
        self._messages = {}

    @contextmanager
    def for_row(self, row):
        """Attribute nested native sends without parsing subjects or message text."""
        previous, self._row = self._row, id(row)
        try:
            yield
        finally:
            self._row = previous

    def send(self, original, bound):
        bound.arguments["self"] = self.private
        count, serial = len(self.private.inbox), self.private.urgent_sound_serial
        try:
            return original(*bound.args, **bound.kwargs)
        finally:
            # Native send_email prepends one message, or leaves the inbox alone
            # when filtering/deduplication rejects it. Keep its own mail logic.
            if len(self.private.inbox) > count:
                message = self.private.inbox[0]
                self._messages[id(message)] = self._row, self.private.urgent_sound_serial - serial

    def changes(self, rows):
        """Select mail for completed records; leave other messages private."""
        return {("mail", index): message for index, message in enumerate(self.private.inbox)
                if self._messages.get(id(message), (None, 0))[0] in rows}

    def checkpoint(self):
        """Expose published messages while retaining pending mail in the view."""
        # The graph has already published selected messages. Their original
        # counterparts now contain the committed data; unpublished messages
        # have no counterpart yet. Edits to messages present before this view
        # also wait for the outer caller, rather than another candidate's result.
        inbox = [self.graph.original(message) for message in self.private.inbox
                 if self.graph.original(message) is not None]
        # A pending operation must not remove an already published message.
        included = {id(message) for message in inbox}
        inbox.extend(message for message in self.live.inbox if id(message) not in included)
        self.live.inbox[:] = inbox
        # Native urgency is a running counter. Its private value includes sends
        # for still-pending records, so withhold those increments with their mail.
        pending = sum(self._messages.get(id(message), (None, 0))[1] for message in self.private.inbox
                      if self.graph.original(message) is None)
        self.live.urgent_sound_serial = self.private.urgent_sound_serial - pending
