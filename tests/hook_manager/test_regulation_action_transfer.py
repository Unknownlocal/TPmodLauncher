# SPDX-License-Identifier: MPL-2.0
"""Logical fragmentation has separate bounds from TCP length framing."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import socket
import time
import threading

import pytest

from hook_manager.regulations.action_transfer import regulation_receive_action, regulation_send_action
from hook_manager.regulations.framing import regulation_read
from hook_manager.regulations.protocol import REGULATION_ACTION_BYTES
from hook_manager.regulations import RegulationChannel
from launcher.regulations.server import RegulationServer


def message():
    owners = {str(index) + "x" * 125: 2**64 - 1 for index in range(32)}
    return {"version": 2, "token": "a" * 64, "id": 2**64 - 1, "epoch": 2**64 - 1, "action": 2**64 - 1,
            "op": "action", "deadline": time.monotonic() + .1, "generations": owners,
            "contexts": [{"padding": "x" * 3500}] * 1024, "selections": [{}], "owners": {}}


def test_fragmented_maximum_action_roundtrips_without_repeating_owner_headers(monkeypatch):
    now = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: now)
    value = message()
    value["selections"] = ["x" * (REGULATION_ACTION_BYTES - len(json.dumps(value, separators=(",", ":")).encode()))]
    assert len(json.dumps(value, separators=(",", ":")).encode()) == REGULATION_ACTION_BYTES
    # This case verifies framing capacity, not timing acceptance. Runtime checks
    # retain the real original deadline; a large transfer cannot renew it.
    sender, receiver = socket.socketpair()
    with sender, receiver, ThreadPoolExecutor(max_workers=1) as worker:
        sender.setblocking(False)
        receiver.setblocking(False)
        def send():
            try:
                regulation_send_action(sender, value)
            finally:
                sender.shutdown(socket.SHUT_WR)  # Also unblock a reader if encoding fails.
        future = worker.submit(send)
        first = regulation_read(receiver, now + .1)
        assert "generations" not in first and first["parts"] == 46
        assert regulation_receive_action(receiver, first) == value
        future.result(timeout=3)


def test_all_legal_maximum_refusals_cross_the_actual_server_and_channel():
    owners = {str(index) + "x" * 125: {"revision": "revision", "rules": ["native"]} for index in range(32)}
    wake = threading.Event()
    expected = {owner: [{"decision": "deny", "message": "\U0001f600" * 160}] * 32 for owner in owners}
    assert len(json.dumps(expected, ensure_ascii=False).encode()) > 256 * 1024
    with closing(RegulationServer({"epoch": 1, "owners": owners, "custom": []}, wake.set)) as server:
        with closing(RegulationChannel(server.endpoint)) as channel, ThreadPoolExecutor(max_workers=1) as worker:
            # This is reply capacity/correlation; real policy admission and Wasm
            # execution are owned by the runtime integration checks.
            channel._generations = dict.fromkeys(owners, 1)
            future = worker.submit(channel, [{}], [{}], {owner: [[0, 0, 0]] * 32 for owner in owners}, time.monotonic() + .1)
            assert wake.wait(timeout=1)
            server.poll(lambda message: expected)
            assert future.result(timeout=1) == expected and not channel.closed


@pytest.mark.parametrize("fault", ["count", "order", "identity", "length", "late"])
def test_invalid_fragment_sets_never_return_a_partial_action(monkeypatch, fault):
    value, frames = message(), []
    value["contexts"] = value["contexts"][:80]
    monkeypatch.setattr("hook_manager.regulations.action_transfer.regulation_write", lambda connection, part, deadline: frames.append(part))
    regulation_send_action(None, value)
    assert len(frames) == 2
    if fault == "count":
        frames[0]["parts"] = 3
    elif fault == "order":
        frames[1]["index"] = 0
    elif fault == "identity":
        frames[1]["epoch"] += 1
    elif fault == "length":
        frames[1]["data"] = frames[1]["data"][:-4]
    elif fault == "late":
        frames[0]["deadline"] = time.monotonic() - 1
    remaining = iter(frames[1:])
    monkeypatch.setattr("hook_manager.regulations.action_transfer.regulation_read", lambda *_: next(remaining))
    with pytest.raises(ValueError):
        regulation_receive_action(None, frames[0])
