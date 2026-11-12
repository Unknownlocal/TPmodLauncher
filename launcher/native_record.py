# SPDX-License-Identifier: MPL-2.0
"""Validate decoded native records before controller state changes."""

from .restart_evidence import valid_restart_evidence


NATIVE_EVENTS = frozenset(("native-activity", "diagnostic", "readiness", "pack", "menu", "settings",
                           "themes", "career-preload", "native-service", "restart", "restart-observation"))


def validate_native_record(record, *, strict=True):
    """Share shape checks across the journal and Frida's native event messages.

    Frida also carries transport/manager messages, whose fields belong to their
    handlers. Journal records must use a recognised native event.
    """
    if type(record) is not dict:
        raise ValueError("Expected a native journal record")
    event = record.get("event", "native-activity")
    if type(event) is not str or strict and event not in NATIVE_EVENTS:
        raise ValueError("Unknown native journal event")
    sequence = record.get("event_sequence")
    if sequence is not None and (type(sequence) is not int or sequence <= 0):
        raise ValueError("Invalid native change sequence")
    if event in ("restart", "restart-observation") and not valid_restart_evidence(event, record.get("status"), sequence):
        raise ValueError("Invalid native restart evidence")
    if (event in ("readiness", "menu", "settings", "themes", "career-preload") and type(record.get("status")) is not dict
            or event == "pack" and type(record.get("pack")) is not dict):
        raise ValueError("Invalid native change data")
    if event in ("native-activity", "diagnostic") and (
            type(record.get("detail")) is not str
            or "fatal" in record and type(record["fatal"]) is not bool
            or "reason" in record and type(record["reason"]) is not str):
        raise ValueError("Invalid native diagnostic data")
    return {**record, "event": event}
