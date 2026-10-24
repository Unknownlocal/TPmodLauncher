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


def action(channel, deadline):
    # Transport-only tests bypass publication; real pack tests own that fence.
    channel._generations = {"owned.rules": 1}
    return channel([{}], [{}], {"owned.rules": [[0, 0, 0]]}, deadline)


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


@pytest.mark.parametrize("fault", ["stale", "deadline", "validation_deadline"])
def test_uncorrelated_or_expired_replies_close_the_channel_without_retry(monkeypatch, fault):
    if fault == "validation_deadline":
        clock = [time.monotonic()]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
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
                try:
                    regulation_write(connection, {**regulation_header(request, check_deadline=False),
                        "id": 0 if fault == "stale" else request["id"], "result": {"owned.rules": [{"decision": "allow"}]}}, time.monotonic() + 1)
                except OSError:
                    pass  # An expired client has already closed its stream.

        future = worker.submit(peer)
        channel = None
        try:
            channel = RegulationChannel({"port": listener.getsockname()[1], "token": token})
            with pytest.raises((HookError, OSError)):
                action(channel, time.monotonic() + .02)
            assert channel.closed
            with pytest.raises(HookError, match="unavailable"):
                action(channel, time.monotonic() + .1)
        finally:
            release.set()
            if channel is not None:
                channel.close()
            future.result(timeout=2)


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

    def paused_accept(listener):
        connection = accept(listener)
        accepted.set()
        release.wait(timeout=2)
        return connection

    def unexpected_read(*args):
        read.set()
        raise RuntimeError("Authentication started after shutdown")

    monkeypatch.setattr(socket.socket, "accept", paused_accept)
    monkeypatch.setattr(server_module, "regulation_read", unexpected_read)
    with closing(RegulationServer(METADATA)) as server:
        with socket.create_connection(("127.0.0.1", server.endpoint["port"]), timeout=1):
            try:
                assert accepted.wait(timeout=1)
                join = server._thread.join

                def release_on_join(timeout=None):
                    # close() has already looked for the not-yet-published socket.
                    release.set()
                    join(timeout=timeout)

                monkeypatch.setattr(server._thread, "join", release_on_join)
                server.close()
                assert not read.is_set() and not server._thread.is_alive()
            finally:
                release.set()
