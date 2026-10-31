# SPDX-License-Identifier: MPL-2.0
"""Owned sockets exercise framing, authentication, correlation and cancellation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import secrets
import json
import socket
import struct
import threading
import time
import weakref

import pytest

from hook_manager.manager import HookError
from hook_manager.game_adapters.regulations import RegulationChannel
from hook_manager.game_adapters.regulations.framing import (
    REGULATION_FRAME_BYTES, regulation_encode, regulation_read, regulation_write,
)
from launcher.regulations.server import RegulationServer
from launcher.regulations import server as server_module
from hook_manager.game_adapters.regulations.protocol import regulation_header


METADATA = {"epoch": 1, "custom": [], "owners": {"owned.rules": {"revision": "revision", "rules": ["native"]}}}


def hello(token):
    return {"version": 2, "op": "hello", "token": token, "id": 0}


def action(channel, deadline, *, count=1):
    # Transport-only tests bypass publication; real pack tests own that fence.
    channel._generations = {"owned.rules": 1}
    return channel([{}], [{}], {"owned.rules": [[slot, 0, 0] for slot in range(count)]}, deadline)


def test_maximum_admitted_action_and_refusals_roundtrip_in_single_frames():
    from mod_runtime._regulations.values import bounded, measure

    # Check capacity separately from 100 ms handler timing. Socket waits still
    # use a finite real deadline so an encoding/read failure cannot hang the test.
    owners = [f"m{i:02}" + "x" * 125 for i in range(32)]
    ctx = {"schema": 1, "scope": "personnel.contract", "operation": "commit", "initiator": "player",
           "clock": {"season": 2026, "absolute_week": 4},
           "effective": {"season": 2026, "start_week": 4, "end_week": None}, "proposal": {}}
    selections = [{"rule": f"r{i:03}" + "x" * 252, "evaluation_season": 2026, "selected": "current",
                   "current": [2**32 - 1, 2026], "upcoming": [2**32 - 1, 10**3000]} for i in range(256)]
    # Large season integers are allowed. Even with one shared proposal they can
    # make a valid action exceed 256 KiB; do not silently restrict those values.
    # Rule IDs have the same width and all values match, so one expanded
    # argument proves the byte/node bounds for every selection in this fixture.
    rule = {"schema": 1, "key": selections[0]["rule"], "revision": f"{'f' * 64}:{2**64 - 1}:{2**64 - 1}",
            "evaluation_season": 2026, "selected": "current",
            "current": {"key": "open", "effective_season": 2026, "parameters": {}, "option_data": {}},
            "upcoming": {"key": "open", "effective_season": 10**3000, "parameters": {}, "option_data": {}}}
    bounded(measure({"context": ctx, "regulation": rule}))
    message = {"version": 2, "token": "a" * 64, "id": 2**64 - 1, "epoch": 2**64 - 1,
               "action": 2**64 - 1, "op": "action", "deadline": .1,
               "generations": dict.fromkeys(owners, 2**64 - 1), "contexts": [ctx], "selections": selections,
               "owners": {owner: {"revision": "f" * 64,
                          "calls": [[i, 0, n * 8 + i] for i in range(8)]} for n, owner in enumerate(owners)}}
    response = {**regulation_header(message, check_deadline=False), "result": {
        owner: [{"decision": "deny", "message": "\U0001f600" * 160}] * 8 for owner in owners}}
    assert 256 * 1024 < len(regulation_encode(message)) < REGULATION_FRAME_BYTES
    sender, receiver = socket.socketpair()
    # Close sockets before joining the writer if the receiver fails early.
    with ThreadPoolExecutor(max_workers=1) as worker, sender, receiver:
        sender.setblocking(False)
        receiver.setblocking(False)
        deadline = time.monotonic() + 3
        for value in (message, response):
            future = worker.submit(regulation_write, sender, value, deadline)
            assert regulation_read(receiver, deadline) == value
            future.result(timeout=3)


@pytest.mark.parametrize("shape", ["multiple-proposals", "owner-call-count", "session-call-count"])
def test_impossible_action_tables_are_rejected_before_queueing(shape):
    count = 1 if shape == "multiple-proposals" else 32
    owners = {f"m{i}": {"revision": "f" * 64, "calls": [[slot, 0, 0] for slot in range(count)]}
              for i in range(9 if shape == "session-call-count" else 1)}
    if shape == "owner-call-count":
        owners["m0"]["calls"].append([32, 0, 0])
    elif shape == "session-call-count":
        owners["m8"]["calls"] = [[0, 0, 0]]  # One call beyond the session's 256-call limit.
    value = {"version": 2, "token": "a" * 64, "id": 1, "epoch": 1, "action": 1, "op": "action",
             "deadline": time.monotonic() + .05, "generations": dict.fromkeys(owners, 1),
             "contexts": [{}] * (2 if shape == "multiple-proposals" else 1), "selections": [{}], "owners": owners}
    with pytest.raises(ValueError, match="single-proposal"):
        regulation_header(value)


def test_length_framing_accepts_fragmented_messages_and_rejects_oversized_prefixes():
    sender, receiver = socket.socketpair()
    with sender, receiver, ThreadPoolExecutor(max_workers=1) as worker:
        sender.setblocking(False)
        receiver.setblocking(False)
        value = {"wire": "x" * 8192}
        data = regulation_encode(value)

        def fragmented():
            sender.setblocking(True)
            for block in (data[:1], data[1:3], data[3:]):
                sender.sendall(block)

        future = worker.submit(fragmented)
        assert regulation_read(receiver, time.monotonic() + 1) == value
        future.result(timeout=1)
        sender.sendall(struct.pack("!I", REGULATION_FRAME_BYTES + 1))
        with pytest.raises(ValueError, match="length"):
            regulation_read(receiver, time.monotonic() + 1)


@pytest.mark.parametrize("fault", ["stale", "deadline", "validation_deadline", "owners", "count", "decision"])
def test_invalid_or_expired_replies_close_the_channel_without_retry(monkeypatch, fault):
    if fault in ("validation_deadline", "owners", "count", "decision"):
        # Keep malformed-reply checks independent of thread scheduling delays.
        clock = [time.monotonic()]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    if fault == "validation_deadline":
        from hook_manager.game_adapters.regulations import channel as channel_module
        decisions = channel_module.regulation_decisions

        def delayed_validation(values, count):
            result = decisions(values, count)
            clock[0] += .2
            return result
        monkeypatch.setattr(channel_module, "regulation_decisions", delayed_validation)
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as worker:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        token, release = secrets.token_hex(32), threading.Event()

        def peer():
            connection, _ = listener.accept()
            with connection:
                connection.setblocking(False)
                assert regulation_read(connection, time.monotonic() + 1) == hello(token)
                regulation_write(connection, {**hello(token), **METADATA}, time.monotonic() + 1)
                request = regulation_read(connection, time.monotonic() + 1)
                if fault == "deadline":
                    release.wait(timeout=2)
                result = {"owned.rules": [{"decision": "allow"}]}
                if fault == "owners":
                    result = {}
                elif fault == "count":
                    result["owned.rules"] = []
                elif fault == "decision":
                    # A valid refusal cannot conceal a malformed later decision.
                    result["owned.rules"] = [{"decision": "deny", "message": "Policy refusal"},
                                             {"decision": "allow", "message": "Unexpected"}]
                try:
                    regulation_write(connection, {**regulation_header(request, check_deadline=False),
                        "id": 0 if fault == "stale" else request["id"], "result": result}, time.monotonic() + 1)
                except OSError:
                    pass  # An expired client has already closed its stream.

        future = worker.submit(peer)
        channel = None
        try:
            channel = RegulationChannel({"port": listener.getsockname()[1], "token": token})
            errors = {"owners": (HookError, "Incomplete regulation owners"),
                      "count": (ValueError, "Incomplete regulation reply"),
                      "decision": (ValueError, "Invalid regulation decision")}
            expected, message = errors.get(fault, ((HookError, OSError), None))
            with pytest.raises(expected, match=message):
                action(channel, time.monotonic() + .02, count=2 if fault == "decision" else 1)
            assert channel.closed
            with pytest.raises(HookError, match="unavailable"):
                action(channel, time.monotonic() + .1)
        finally:
            release.set()
            if channel is not None:
                channel.close()
            future.result(timeout=2)


@pytest.mark.parametrize("reply", [
    {"A": {"choice": "current", "reason": "Valid"}},
    {"A": {"choice": "current", "reason": "Valid"}, "B": {"choice": "abstain", "reason": "Invalid"}},
], ids=("missing-team", "invalid-later-vote"))
def test_vote_reply_is_complete_and_valid_before_leaving_the_channel(reply):
    metadata = {**METADATA, "owners": {"owned.rules": {
        **METADATA["owners"]["owned.rules"], "voting": ["native"]}}}
    wake = threading.Event()
    with closing(RegulationServer(metadata, wake.set)) as server, ThreadPoolExecutor(max_workers=1) as worker:
        with closing(RegulationChannel(server.endpoint)) as channel:
            # Isolate reply admission; pack tests exercise policy publication.
            channel._generations = {"owned.rules": 1}
            future = worker.submit(channel.vote, {}, {"key": "native"}, [{"team": "A"}, {"team": "B"}])
            assert wake.wait(timeout=1)
            server.poll(lambda message: reply)
            with pytest.raises(HookError, match="Voting unavailable") as raised:
                future.result(timeout=1)
            assert isinstance(raised.value.__cause__, ValueError) and channel.closed


def test_unauthenticated_connection_cannot_latch_a_required_owner_or_retain_completed_actions(monkeypatch):
    wake = threading.Event()
    retired, requests, idle_reads = threading.Event(), [], []
    encodings, dumps = [], json.dumps
    read, request_type = server_module.regulation_read, server_module.TransportRequest

    def track_request(message):
        request = request_type(message)
        requests.append(weakref.ref(request))
        return request

    def track_idle(connection, deadline, **options):
        if deadline is None:
            idle_reads.append(True)
            if len(idle_reads) == 2:
                retired.set()
        return read(connection, deadline, **options)

    def track_encoding(value, **options):
        if type(value) is dict and value.get("op") == "action" and "contexts" in value:
            encodings.append(True)
        return dumps(value, **options)

    monkeypatch.setattr(server_module, "TransportRequest", track_request)
    monkeypatch.setattr(server_module, "regulation_read", track_idle)
    monkeypatch.setattr(json, "dumps", track_encoding)
    with closing(RegulationServer(METADATA, wake.set)) as server:
        with socket.create_connection(("127.0.0.1", server.endpoint["port"]), timeout=1) as impostor:
            impostor.setblocking(False)
            regulation_write(impostor, {"token": "\u00e9" * 64, "id": 0}, time.monotonic() + 1)
            with pytest.raises(ConnectionError):
                regulation_read(impostor, time.monotonic() + 1)
        with closing(RegulationChannel(server.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            assert server.connected.is_set() and not server.failed.is_set()
            deadline, evaluated = time.monotonic() + .1, []
            future = worker.submit(action, channel, deadline)
            assert wake.wait(timeout=1)

            def evaluate(message):
                evaluated.append((message["owners"], message["deadline"], threading.get_ident()))
                return {"owned.rules": [{"decision": "allow"}]}

            server.poll(evaluate)
            assert future.result(timeout=1) == {"owned.rules": [{"decision": "allow"}]}
            assert evaluated == [({"owned.rules": {"revision": "revision", "calls": [[0, 0, 0]]}}, deadline, threading.get_ident())]
            assert not channel.closed and not server.failed.is_set()
            assert retired.wait(timeout=1) and len(requests) == 1 and requests[0]() is None
            assert len(encodings) == 1


def test_shutdown_releases_a_pending_request_without_entering_an_evaluator():
    wake = threading.Event()
    with closing(RegulationServer(METADATA, wake.set)) as server:
        with closing(RegulationChannel(server.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            # A policy wait lasts six seconds: socket shutdown alone cannot
            # release its controller Event within close()'s one-second join.
            future = worker.submit(channel.publish, native={"native": [{"key": "open"}]})
            assert wake.wait(timeout=1)
            server.close()
            with pytest.raises((OSError, HookError)):
                future.result(timeout=1)
            assert channel.closed and not server._thread.is_alive() and server._requests.empty()


def test_shutdown_closes_a_socket_accepted_before_connection_publication(monkeypatch):
    accepted, release, read = threading.Event(), threading.Event(), threading.Event()
    accept = socket.socket.accept
    target_listener = None
    connections = []

    def paused_accept(listener):
        connection = accept(listener)
        # Windows also accepts a connection while constructing socketpair().
        # Gate only the server listener, after accept but before publication.
        if listener is target_listener:
            connections.append(connection[0])  # Keep GC from concealing a missing close.
            accepted.set()
            release.wait(timeout=2)
        return connection

    def unexpected_read(*args, **kwargs):
        read.set()
        raise RuntimeError("Authentication started after shutdown")

    monkeypatch.setattr(socket.socket, "accept", paused_accept)
    monkeypatch.setattr(server_module, "regulation_read", unexpected_read)
    with closing(RegulationServer(METADATA)) as server:
        target_listener = server._listener
        with socket.create_connection(("127.0.0.1", server.endpoint["port"]), timeout=1) as peer:
            try:
                assert accepted.wait(timeout=1)
                assert server._connection is None and not read.is_set()
                join = server._thread.join

                def release_on_join(timeout=None):
                    # close() has already looked for the not-yet-published socket.
                    release.set()
                    join(timeout=timeout)

                monkeypatch.setattr(server._thread, "join", release_on_join)
                server.close()
                assert not read.is_set() and not server._thread.is_alive()
                assert peer.recv(1) == b""
            finally:
                release.set()
                for connection in connections:
                    connection.close()
