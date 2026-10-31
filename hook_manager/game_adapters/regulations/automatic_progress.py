# SPDX-License-Identifier: MPL-2.0
"""Keep seasonal decision progress and undelivered notices with one career."""

from copy import deepcopy
import json

from ...manager import HookError

FIELD = "mod_automatic_changes"


class AutomaticProgress:
    """Retain saved data even when its rules are disabled or its format is unknown.

    Invalid but bounded JSON is preserved for saving and marked unavailable for
    automatic decisions. Data that cannot be copied safely must not be silently
    discarded by a subsequent save. Native rule choices are stored elsewhere.
    """

    def __init__(self, saved):
        self.present = type(saved) is dict and FIELD in saved
        self.data = {"schema": 1, "categories": {}}
        self.error = None
        self._preservable = not self.present
        # Count the empty root, schema value and category map once.
        self._bytes, self._nodes = len('{"schema":1,"categories":{}}'), 3
        if not self.present:
            return
        try:
            raw = saved[FIELD]
            # This runs once on load. Saves copy the retained data without
            # serialising, decoding or entering a mod interpreter again.
            pending = [(raw, 0)]
            nodes = 0
            while pending:
                value, depth = pending.pop()
                nodes += 1
                if nodes > 32768 or depth > 8:
                    raise HookError("Automatic progress exceeds its node or depth limit")
                if type(value) in (dict, list) and nodes + len(pending) + len(value) > 32768:
                    raise HookError("Automatic progress exceeds its node limit")
                if type(value) is dict and all(type(key) is str for key in value):
                    pending.extend((item, depth + 1) for item in value.values())
                elif type(value) is list:
                    pending.extend((item, depth + 1) for item in value)
                elif type(value) not in (str, int, float, bool, type(None)):
                    raise HookError("Automatic progress must contain plain JSON values")
            size = len(json.dumps(raw, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
            if size > 1024 * 1024:
                raise HookError("Automatic progress exceeds its 1 MiB limit")
            self.data, self._preservable = deepcopy(raw), True
            self._validate()
            self._bytes, self._nodes = size, nodes
        except (HookError, ValueError, TypeError, RecursionError) as error:
            self.error = str(error)

    def _validate(self):
        """Check the supported format without treating unknown data as empty."""
        data = self.data
        if (type(data) is not dict or set(data) != {"schema", "categories"}
                or type(data["schema"]) is not int or data["schema"] != 1):
            raise HookError("Unsupported automatic progress format")
        categories = data["categories"]
        if type(categories) is not dict or len(categories) > 128:
            raise HookError("Invalid automatic progress category map")
        for key, record in categories.items():
            if (not 1 <= len(key) <= 256 or type(record) is not dict
                    or set(record) != {"checked_season", "pending_announcements"}
                    or type(record["checked_season"]) is not int or record["checked_season"] < 1):
                raise HookError("Invalid automatic progress season record")
            notices = record["pending_announcements"]
            if type(notices) is not list or len(notices) > 32:
                raise HookError("Invalid automatic progress announcement list")
            seasons = set()
            for notice in notices:
                if (type(notice) is not dict or set(notice) != {"decision_season", "option"}
                        or type(notice["decision_season"]) is not int
                        or not 1 <= notice["decision_season"] <= record["checked_season"]
                        or notice["decision_season"] in seasons
                        or type(notice["option"]) is not str or not 1 <= len(notice["option"]) <= 128):
                    raise HookError("Invalid automatic progress announcement")
                seasons.add(notice["decision_season"])

    def snapshot(self):
        """Copy preserved progress; refuse a save that would lose unsafe data."""
        if not self._preservable:
            raise HookError("Cannot safely preserve automatic progress: " + self.error)
        return deepcopy(self.data)

    def check_record(self, key, record):
        """Check room before native staging, measuring only the affected record.

        Loaded progress has already passed format checks. Internal writers keep
        that shape and update these totals rather than encoding the entire save.
        """
        if self.error:
            raise HookError("Automatic progress is unavailable: " + self.error)
        records = self.data["categories"]
        old = records.get(key)
        if ((old is None and len(records) >= 128)
                or len(record["pending_announcements"]) > 32):
            raise HookError("Automatic progress has no room for another category or notice")
        encode = lambda value: len(json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")))
        size = self._bytes + encode(record)
        # A record counts its dict, season and list; each notice adds its dict,
        # season and option. Object keys are excluded, as in the load-time count.
        nodes = self._nodes + 3 + 3 * len(record["pending_announcements"])
        if old is None:
            size += encode(key) + 1 + bool(records)
        else:
            size -= encode(old)
            nodes -= 3 + 3 * len(old["pending_announcements"])
        if size > 1024 * 1024 or nodes > 32768:
            raise HookError("Automatic progress exceeds its byte or node limit")
        return size, nodes

    def store_record(self, key, record):
        """Retain one checked season and its still-undelivered announcements."""
        self._bytes, self._nodes = self.check_record(key, record)
        self.data["categories"][key] = record
        self.present = True
