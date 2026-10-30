# SPDX-License-Identifier: MPL-2.0
"""Check voting inputs and replies separately from signing decisions."""

from .policy import fields
from .values import integer, loads, measure, text, scalar, array_measure, object_measure, bounded

PACKET_LIMITS = (384 * 1024, 81920, 16)
SNAPSHOT_LIMITS = (64 * 1024, 8192, 12)


def vote_result(value):
    """Accept a choice and explanation, without inventing native score components."""
    fields(value, ("choice", "reason"))
    reason = value["reason"]
    if (type(value["choice"]) is not str or value["choice"] not in ("current", "proposed")
            or type(reason) is not str or not reason.strip()
            or len(reason.encode("utf-8")) > 2048
            or any(not char.isprintable() and char not in "\n\t" for char in reason)):
        raise ValueError("Invalid regulation vote result")
    return dict(value)


def voting_packet(packet):
    """Bound the shared snapshot once and each team's smaller calculation inputs."""
    fields(packet, ("owner", "binding", "snapshot", "regulation", "contexts"))
    text(packet["owner"], 128)
    integer(packet["binding"])
    snapshot, rule, contexts = packet["snapshot"], packet["regulation"], packet["contexts"]
    fields(snapshot, ("voting_model_version", "season", "teams", "results", "field", "projected_races"))
    integer(snapshot["voting_model_version"], positive=True)
    integer(snapshot["season"], positive=True)
    integer(snapshot["projected_races"])
    if type(snapshot["teams"]) is not dict or type(snapshot["results"]) is not list or type(snapshot["field"]) is not dict:
        raise ValueError("Invalid frozen voting snapshot")
    snapshot_metric = measure(snapshot, limited=True, limits=SNAPSHOT_LIMITS)
    fields(rule, ("key", "current", "proposed"))
    text(rule["key"], 256)
    for option in (rule["current"], rule["proposed"]):
        if type(option) is not dict:
            raise ValueError("Expected a voting option object")
        text(option.get("key"), 128)
    if rule["current"]["key"] == rule["proposed"]["key"]:
        raise ValueError("A voting proposal needs a different option")
    rule_metric = measure(rule, limited=True, limits=(16 * 1024, 2048, 12))
    if type(contexts) is not list or not 1 <= len(contexts) <= 64:
        raise ValueError("Invalid voting calculation count")
    teams, identity = set(), None
    context_metrics = []
    for context in contexts:
        fields(context, ("schema", "review", "proposal", "decision_season", "effective_season", "team", "native", "seed"))
        if type(context["schema"]) is not int or context["schema"] != 1:
            raise ValueError("Unsupported voting context")
        for key in ("review", "proposal", "seed"):
            text(context[key], 512)
        current = tuple(context[key] for key in ("review", "proposal", "seed"))
        if identity is not None and current != identity:
            raise ValueError("Mixed voting proposal identities")
        identity = current
        text(context["team"], 128)
        integer(context["decision_season"], positive=True)
        integer(context["effective_season"], positive=True)
        if (context["decision_season"] != snapshot["season"]
                or context["effective_season"] != context["decision_season"] + 1
                or context["team"] in teams or type(context["native"]) is not dict):
            raise ValueError("Foreign or repeated voting context")
        # Native evaluation may also contain score/components. Its choice/reason
        # remain available as a fallback without making those extra fields output.
        vote_result({key: context["native"].get(key) for key in ("choice", "reason")})
        context_metrics.append(measure(context, limited=True))
        teams.add(context["team"])
    # Every field was checked above. Combine its exact measurements instead of
    # traversing and encoding the snapshot and contexts a second time.
    return bounded(object_measure((("owner", scalar(packet["owner"])), ("binding", scalar(packet["binding"])),
                                  ("snapshot", snapshot_metric), ("regulation", rule_metric),
                                  ("contexts", array_measure(context_metrics)))), limits=PACKET_LIMITS)


def vote_reply(value, action, slot):
    """Check the addressed reply; escaped multiline reasons may exceed 4 KiB."""
    if type(value) is not str or len(value.encode("utf-8")) > 8192:
        raise ValueError("Voting reply exceeds its bound")
    packet = loads(value)
    fields(packet, ("action", "slot", "result"))
    if integer(packet["action"], 64, positive=True) != action or integer(packet["slot"]) != slot:
        raise ValueError("Foreign voting reply")
    return vote_result(packet["result"])
