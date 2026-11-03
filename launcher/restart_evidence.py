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
    return (set(status) in ({"state", "targets", "groups", "errors"}, {"state", "targets", "groups", "errors", "final"})
            and ("final" not in status or type(status["final"]) is bool)
            and status["state"] in ("ready", "stopped")
            and type(status["targets"]) is int and status["targets"] == 2
            and type(status["groups"]) is dict
            and set(status["groups"]) == {"restart.settings", "restart.mod-pack"}
            and all(state in ("pending", "active", "failed", "removed") for state in status["groups"].values())
            and type(status["errors"]) is list and all(type(error) is str for error in status["errors"]))


def replacement_decision(bridge):
    """Trust positive intent; trust its absence only after a complete final check."""
    attempt = getattr(bridge, "restart_attempt", None)
    if attempt is not None and attempt["state"] in ("requested", "committed"):
        return "restart", f"{attempt['source']} attempt={attempt['attempt']} state={attempt['state']}"
    coverage = getattr(bridge, "restart_observation", None)
    reporting = getattr(bridge, "reporting", None)
    journal = getattr(bridge, "journal", None)
    if (coverage is not None and coverage.get("final") is True and coverage["state"] == "ready"
            and not coverage["errors"] and all(state == "active" for state in coverage["groups"].values())
            and reporting is not None and reporting.confirmed and journal is not None
            and not journal.retained and not journal.read_failed):
        return "ordinary", "final restart observation and reporting complete; no restart pending"
    return "unknown", "restart observation or final reporting incomplete"
