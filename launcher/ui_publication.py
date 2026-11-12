# SPDX-License-Identifier: MPL-2.0
"""Publish changed mod registries together while tracking each acknowledgement."""

from copy import deepcopy

from .diagnostics import exception_from_record
from .mod_contract import ManifestError


class UiPublication:
    """Own applied registries and generations; uncertain replies require rollback."""

    def __init__(self, send):
        self.send = send
        self.applied = {area: [] for area in ("menu", "settings", "themes")}
        self.generations = dict.fromkeys(self.applied, 0)
        self.failed = {}

    def publish(self, values, *, settings_result=None):
        self.failed = {}
        if self.send is None:
            return
        changed = {area: deepcopy(records) for area, records in values.items()
                   if self.applied[area] != records or area == "settings" and settings_result is not None}
        if not changed:
            return
        for area in changed:
            self.applied[area] = None
        snapshot = dict(changed)
        if "settings" in changed:
            snapshot["settings"] = {"records": changed["settings"], "result": settings_result}
        try:
            response = self.send(snapshot)
            for area, records in changed.items():
                if response is not None:
                    status = response[area]
                    self.generations[area] = status["generation"]
                    if status["state"] == "failed":
                        self.failed[area] = status
                        continue
                self.applied[area] = records
        except Exception as error:
            # A failed send can have applied any part of the snapshot. Retain
            # every affected area for rejection and a later clearing request.
            self.failed = {area: {"generation": self.generations[area], "state": "failed", "detail": str(error)}
                           for area in changed}
            raise
        if self.failed:
            detail = next(iter(self.failed.values())).get("detail", "Native UI unavailable")
            raise exception_from_record(detail, self.failed, ManifestError)

    def reject(self, area, status):
        """Ignore a deferred failure from an older registry generation."""
        if status.get("generation") != self.generations[area] or status.get("state") != "failed":
            return False
        self.applied[area] = None
        return True
