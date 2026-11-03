# SPDX-License-Identifier: MPL-2.0
"""Check plain restart evidence before it is retained for a tracked process."""


def valid_restart_evidence(event, status, sequence):
    """Reject incomplete markers and coverage that could hide a missing observer."""
    if type(status) is not dict or type(sequence) is not int or sequence <= 0:
        return False
    if event == "restart":
        return (set(status) == {"attempt", "source", "series", "state"}
                and type(status["attempt"]) is int and status["attempt"] > 0
                and status["source"] in ("settings", "mod-pack")
                and status["series"] in (None, "formula", "oval")
                and status["state"] in ("requested", "committed", "cancelled"))
    return (set(status) == {"state", "targets", "groups", "errors"}
            and status["state"] in ("ready", "stopped")
            and type(status["targets"]) is int and status["targets"] == 2
            and type(status["groups"]) is dict
            and set(status["groups"]) == {"restart.settings", "restart.mod-pack"}
            and all(state in ("pending", "active", "failed", "removed") for state in status["groups"].values())
            and type(status["errors"]) is list and all(type(error) is str for error in status["errors"]))
