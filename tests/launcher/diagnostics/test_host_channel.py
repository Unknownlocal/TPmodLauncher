# SPDX-License-Identifier: MPL-2.0
"""Exercise explicit endpoint startup and EOF with real owned FIFO descriptors."""

import os
import queue
import select
import subprocess
import sys
import threading
import time

import pytest

from launcher.diagnostics.host_channel import HostChannel
from launcher.diagnostics.host_lifetime import HostLifetime

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native FIFO tests; Wine access is accepted separately on Mint")
SESSION = "linux-notice.0123456789"


@pytest.fixture
def endpoints(tmp_path):
    directory = tmp_path / ".state" / SESSION
    directory.mkdir(parents=True)
    for name in ("owner", "control"):
        os.mkfifo(directory / name, 0o600)
    owner = os.open(directory / "owner", os.O_RDWR)
    control = os.open(directory / "control", os.O_RDWR)
    yield tmp_path, owner, control
    os.close(owner)
    os.close(control)


def receive(descriptor):
    line = bytearray()
    deadline = time.monotonic() + 2
    while not line.endswith(b"\n"):
        ready, _, _ = select.select([descriptor], [], [], max(0, deadline - time.monotonic()))
        assert ready, "Owned FIFO peer did not send a complete frame"
        part = os.read(descriptor, 1)
        assert part, bytes(line)
        line.extend(part)
    return line.decode("ascii")


def test_handshake_private_handles_and_lifetime_do_not_leak_into_children(endpoints):
    root, owner, control = endpoints
    completed = queue.Queue()
    thread = threading.Thread(target=lambda: completed.put(HostChannel(root, SESSION)), daemon=True)
    thread.start()
    assert receive(control) == f"ML1 {SESSION} HELLO\n"
    os.write(owner, f"ML1 {SESSION} START\n".encode())
    assert receive(control) == f"ML1 {SESSION} READY\n"
    channel = completed.get(timeout=2)
    lifetime = HostLifetime(descriptor=channel.reader)
    assert not os.get_inheritable(channel.reader) and not os.get_inheritable(channel.writer)
    # close_fds=False would reveal accidentally inheritable descriptors. The
    # child remains alive while both protocol directions are checked for EOF.
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
                             stdin=subprocess.PIPE, close_fds=False)
    try:
        channel.send("DONE", 37)
        assert receive(control) == f"ML1 {SESSION} DONE 37\n"
        channel.close()
        # Drop the fixture's keepers; temporary test duplicates are made below
        # so fixture teardown can still close its original descriptor numbers.
        reader = os.open(root / ".state" / SESSION / "control", os.O_RDONLY | os.O_NONBLOCK)
        with open(os.devnull, "rb") as null:
            os.dup2(null.fileno(), control)
            os.dup2(null.fileno(), owner)
        assert os.read(reader, 1) == b""
        os.close(reader)
        assert lifetime.lost.wait(1)
        assert child.poll() is None
    finally:
        child.stdin.close()
        child.wait(timeout=3)
        channel.close()


@pytest.mark.parametrize("challenge", [b"ML2 wrong START\n", b"x" * 256])
def test_invalid_or_unterminated_challenge_cannot_start_launcher(endpoints, challenge):
    root, owner, control = endpoints
    result = queue.Queue()
    def connect():
        try:
            HostChannel(root, SESSION)
        except Exception as error:
            result.put(error)
    threading.Thread(target=connect, daemon=True).start()
    assert receive(control) == f"ML1 {SESSION} HELLO\n"
    os.write(owner, challenge)
    assert isinstance(result.get(timeout=2), ValueError)


def test_missing_startup_peer_is_bounded_before_game_entry(endpoints):
    root, owner, control = endpoints
    with pytest.raises(TimeoutError, match="handshake"):
        HostChannel(root, SESSION, startup_timeout=.05)
    # Release the timed-out daemon's pending read; it must close its endpoints
    # rather than starting entry after the caller has already refused startup.
    assert receive(control) == f"ML1 {SESSION} HELLO\n"
    os.write(owner, b"invalid\n")
