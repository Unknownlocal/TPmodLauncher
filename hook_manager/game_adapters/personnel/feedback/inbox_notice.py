# SPDX-License-Identifier: MPL-2.0
"""Show a signing refusal for the inbox acceptance currently being handled."""

from ...ui import ScrollableDialog


class InboxNotice:
    kind = "inbox"

    def __init__(self, ui):
        self.dialog = ScrollableDialog(ui)
        self.reason, self.candidate = None, ""

    def execute(self, screen, action, original, local):
        """Associate only this Accept action with signing feedback, then restore it."""
        if action.get("type") != "personnel_contract_decision":
            return original()
        previous = getattr(local, "personnel_screen", None)
        local.personnel_screen = None
        try:
            self.reason, self.dialog.open, self.candidate = None, False, ""
            if str(action.get("choice") or "").lower() == "accept":
                manager = getattr(screen.state, "personnel_negotiation_manager", None)
                decision = getattr(manager, "pending_player_decisions", {}).get(str(action.get("decision_id") or ""), {})
                self.candidate = str(decision.get("candidate_name") or "")
                self.dialog.body.reset()
                local.personnel_screen = screen
            # Declines can try backup offers; their refusals are not this
            # player's acceptance and must not open a dialog on this screen.
            return original()
        finally:
            local.personnel_screen = previous

    def refuse(self, reason):
        self.reason, self.dialog.open = reason, True

    def handle_event(self, screen, event):
        """Keep input inside an open dialog, including the event that closes it."""
        return self.dialog.handle_event(screen, event)

    def draw(self, screen, surface, original):
        """Draw the native inbox once, then overlay the current refusal if open."""
        result = original()
        if self.dialog.open:
            self.dialog.draw(screen, surface, title="Contract cannot be signed", body=self.reason,
                             subtitle=self.candidate, footer="The agreed contract is still awaiting your decision.")
        return result
