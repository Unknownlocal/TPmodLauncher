# SPDX-License-Identifier: MPL-2.0
"""Owned sockets exercise framing, authentication, correlation and cancellation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import secrets
import socket
import struct
import threading
import time

import pytest

from hook_manager.manager import HookError
from hook_manager.regulations import RegulationChannel
from hook_manager.regulations.framing import (
    REGULATION_FRAME_BYTES, regulation_encode, regulation_read, regulation_write,
)
from launcher.regulations.server import RegulationServer
from launcher.regulations import server as server_module


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


@pytest.mark.parametrize("fault", ["stale", "deadline"])
def test_uncorrelated_or_expired_replies_close_the_channel_without_retry(fault):
    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as worker:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        token, release = secrets.token_hex(32), threading.Event()

        def peer():
            connection, _ = listener.accept()
            with connection:
                connection.setblocking(False)
                assert regulation_read(connection, time.monotonic() + 1) == {"token": token, "id": 0}
                regulation_write(connection, {"token": token, "id": 0, "bound": True}, time.monotonic() + 1)
                request = regulation_read(connection, time.monotonic() + 1)
                if fault == "deadline":
                    release.wait(timeout=2)
                try:
                    regulation_write(connection, {"token": token, "id": 0 if fault == "stale" else request["id"],
                        "owner": "owned.rules", "decisions": [{"decision": "allow"}]}, time.monotonic() + 1)
                except OSError:
                    pass  # An expired client has already closed its stream.

        future = worker.submit(peer)
        channel = None
        try:
            channel = RegulationChannel({"port": listener.getsockname()[1], "token": token})
            with pytest.raises((HookError, OSError)):
                channel("owned.rules", [{}], time.monotonic() + .02)
            assert channel.closed
            with pytest.raises(HookError, match="unavailable"):
                channel("owned.rules", [{}], time.monotonic() + .1)
        finally:
            release.set()
            if channel is not None:
                channel.close()
            future.result(timeout=2)


def test_unauthenticated_connection_cannot_latch_a_required_owner():
    wake = threading.Event()
    with closing(RegulationServer({"owned.rules"}, wake.set)) as server:
        with socket.create_connection(("127.0.0.1", server.endpoint["port"]), timeout=1) as impostor:
            impostor.setblocking(False)
            regulation_write(impostor, {"token": "\u00e9" * 64, "id": 0}, time.monotonic() + 1)
            with pytest.raises(ConnectionError):
                regulation_read(impostor, time.monotonic() + 1)
        with closing(RegulationChannel(server.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            assert server.connected.is_set() and not server.failed.is_set()
            deadline, evaluated = time.monotonic() + .1, []
            future = worker.submit(channel, "owned.rules", [{}], deadline)
            assert wake.wait(timeout=1)

            def evaluate(owner, calls, expires):
                evaluated.append((owner, calls, expires, threading.get_ident()))
                return [{"decision": "allow"}]

            server.poll(evaluate)
            assert future.result(timeout=1) == [{"decision": "allow"}]
            assert evaluated == [("owned.rules", [{}], deadline, threading.get_ident())]
            assert not channel.closed and not server.failed.is_set()


def test_shutdown_releases_a_pending_request_without_entering_an_evaluator():
    wake = threading.Event()
    with closing(RegulationServer({"owned.rules"}, wake.set)) as server:
        with closing(RegulationChannel(server.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(channel, "owned.rules", [{}], time.monotonic() + .1)
            assert wake.wait(timeout=1)
            server.close()
            with pytest.raises((OSError, HookError)):
                future.result(timeout=1)
            assert channel.closed and not server._thread.is_alive()


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
    with closing(RegulationServer({"owned.rules"})) as server:
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
