# SPDX-License-Identifier: MPL-2.0
"""Deliver saved automatic decisions through the game's ordinary regulation mail."""

from ...diagnostics import build_log_record

from ...callables import CallableTarget
from ...manager import HookError


class AutomaticAnnouncements:
    """Keep a notice pending until its identity is present in the native inbox."""

    def __init__(self, categories, submit_log):
        target = CallableTarget("regulations.automatic.mail", "managers.formula_regulation_governance",
                                ("FormulaRegulationGovernanceManager",), "_send",
                                "(self, subject, body, actions=None, *, structured_content=None)")
        if target.resolve(categories.regulation_changes.completed) is None:
            raise HookError("Automatic announcements need the loaded native mail method")
        self.categories, self.submit_log = categories, submit_log

    def check(self, state):
        """Require a readable inbox and its native delivery entry before activation."""
        mail = getattr(state, "email_manager", None)
        if type(getattr(mail, "inbox", None)) is not list or not callable(getattr(mail, "send_email", None)):
            raise HookError("Automatic announcements need the career's native inbox")

    def deliver(self, manager, key, *, season=None):
        """Retry saved notices independently; one failed delivery cannot stop others."""
        progress = manager._tpm_automatic_progress
        if progress.error:
            return
        record = progress.data["categories"].get(key)
        if record is None:
            return
        for notice in record["pending_announcements"]:
            if season is not None and notice["decision_season"] != season:
                continue
            try:
                self._deliver(manager, key, notice)
            except Exception as error:
                self.submit_log(build_log_record('ERROR', "REGULATIONS COMPATIBILITY FAILED automatic announcement " + key + ": "
                            + str(error)[:512] + "; insertion checked; unconfirmed notices retained", error=error, component="regulations"))

    def _deliver(self, manager, key, notice):
        """Confirm insertion even if native sending raises after storing the mail."""
        mail = manager.state.email_manager
        season = notice["decision_season"]
        # Leave the native message type unset so the ordinary body renderer is
        # used. This private marker identifies deliveries without a new UI format.
        content = {"kind": "mod_automatic_regulation", "rule": key, "decision_season": season,
                   "decision_id": f"automatic:{key}:{season}"}

        def inserted():
            return any(type(row) is dict and type(row.get("structured_content")) is dict
                       and all(row["structured_content"].get(field) == value for field, value in content.items())
                       for row in mail.inbox)

        try:
            if not inserted():
                category = self.categories.categories[key]
                option = next(row for row in category["options"] if row["key"] == notice["option"])
                label, name = category.get("label", key), option.get("name", notice["option"])
                body = f"{label}: {name}\n\nEffective from Season {season + 1}."
                summary = option.get("description") or option.get("summary")
                if summary:
                    body += "\n\n" + summary
                # No action or special renderer is needed: the native mailbox
                # displays this ordinary subject/body, including after rollover.
                manager._send("Regulation change: " + label, body, structured_content=content)
        finally:
            confirmed = inserted()
            if confirmed:
                progress = manager._tpm_automatic_progress
                record = progress.data["categories"][key]
                progress.store_record(key, dict(record, pending_announcements=[
                    row for row in record["pending_announcements"] if row["decision_season"] != season]))
        if not confirmed:
            raise HookError("Native mail insertion was not confirmed")
