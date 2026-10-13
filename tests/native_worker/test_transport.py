# SPDX-License-Identifier: MPL-2.0
"""Exercise real Core transport, attachment limits and cleanup on pipe loss."""

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

import pytest
from launcher.native_worker import (
    Backend,
    Cancellable,
    Worker,
    WorkerError,
    verify_worker,
    target_name,
)
from launcher.process_lifecycle import Controller, LaunchError

TARGET = Path(__file__).with_name("fixtures") / "transport_target.py"


def test_script_messages_and_game_output_use_separate_channels(
    worker_executable, tmp_path
):
    messages, output = queue.Queue(), queue.Queue()
    worker = Worker(worker_executable, output=lambda pid, fd, data: output.put(data))
    device = worker.device
    try:
        # Even an existing process owned by this test cannot be attached outside the spawned chain.
        with pytest.raises(WorkerError, match="outside"):
            device.attach(os.getpid())
        pid = device.spawn(
            [sys.executable, "-B", str(TARGET), str(tmp_path / "started")],
            envp=os.environ.copy(),
            cwd=str(tmp_path),
            stdio="inherit",
        )
        session = device.attach(pid)
        assert not (tmp_path / "started").exists(), "Target ran before attach/resume"
        script = session.create_script(
            'send({state: "loaded"}); recv("echo", m => send(m.value));',
            name="owned-test",
        )
        script.on("message", lambda message, data: messages.put(message))
        script.load()
        assert messages.get(timeout=5)["payload"] == {"state": "loaded"}
        script.post({"type": "echo", "value": 'spaces, quotes " and Unicode é'})
        assert messages.get(timeout=5)["payload"] == 'spaces, quotes " and Unicode é'
        device.resume(pid)
        data = b""
        deadline = time.monotonic() + 5
        while b"ordinary target output" not in data:
            data += output.get(timeout=max(0, deadline - time.monotonic()))
        script.unload()
        session.detach()
        assert script.is_destroyed and session.is_detached
        replacement = device.attach(pid)
        assert replacement.handle != session.handle
        with pytest.raises(WorkerError, match="Unknown native handle"):
            worker.request("session.state", {"session": session.handle})
        assert not replacement.is_detached
        replacement.detach()
    finally:
        worker.close()


def test_input_eof_releases_a_suspended_root_without_killing_it(
    worker_executable, tmp_path
):
    process = subprocess.Popen(
        [str(worker_executable)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    marker = tmp_path / "released"
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "spawn",
        "timeout_ms": 5000,
        "params": {
            "argv": [sys.executable, "-B", str(TARGET), str(marker)],
            "envp": [f"{k}={v}" for k, v in os.environ.items()],
            "cwd": str(tmp_path),
        },
    }
    try:
        process.stdin.write(json.dumps(request).encode() + b"\n")
        process.stdin.flush()
        frames = queue.Queue()
        reader = threading.Thread(
            target=lambda: frames.put(process.stdout.readline()), daemon=True
        )
        reader.start()
        response = json.loads(frames.get(timeout=8))
        assert "result" in response, response
        assert not marker.exists(), "Root was not suspended"
        process.stdin.close()
        assert process.wait(timeout=10) == 0, process.stderr.read().decode()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.read_text(encoding="utf-8") == "released"
    finally:
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # Bound a failed test by stopping only its infrastructure helper.
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def test_core_call_timeout_can_be_cancelled_then_retried(worker_executable, tmp_path):
    worker = Worker(worker_executable, output=lambda *args: None)
    messages = queue.Queue()
    try:
        pid = worker.device.spawn(
            [sys.executable, "-B", str(TARGET), str(tmp_path / "released")],
            envp=os.environ.copy(),
            cwd=str(tmp_path),
            stdio="inherit",
        )
        session = worker.device.attach(pid)
        # Block script loading in the agent's receive loop, not in the target's Python thread.
        script = session.create_script(
            'recv("release", () => send({released: true})).wait();',
            name="owned-timeout",
        )
        script.on("message", lambda message, data: messages.put(message))
        started = time.monotonic()
        with pytest.raises(WorkerError, match="cancel"):
            worker.request("script.load", {"script": script.handle}, timeout=0.05)
        assert time.monotonic() - started < 3
        # The cancelled Core wait must not leave the transport unable to accept the release.
        script.post({"type": "release"})
        assert messages.get(timeout=5)["payload"] == {"released": True}
        worker.device.resume(pid)
        script.unload()
        session.detach()
    finally:
        worker.close()


def test_cancelled_spawn_is_not_sent_and_does_not_consume_root_ownership(
    worker_executable, tmp_path
):
    worker = Worker(worker_executable, output=lambda *args: None)
    arguments = [sys.executable, "-B", str(TARGET), str(tmp_path / "released")]
    try:
        cancellation = Cancellable()
        cancellation.cancel()
        with cancellation, pytest.raises(WorkerError, match="cancelled before sending"):
            worker.device.spawn(
                arguments, envp=os.environ.copy(), cwd=str(tmp_path), stdio="inherit"
            )
        assert not (tmp_path / "released").exists()
        # Only one root is permitted; success here proves the cancelled spawn never reached Core.
        pid = worker.device.spawn(
            arguments, envp=os.environ.copy(), cwd=str(tmp_path), stdio="inherit"
        )
        worker.device.resume(pid)
    finally:
        worker.close()


def test_worker_hash_is_checked_before_execution(tmp_path):
    directory = tmp_path / "libs/core_worker" / target_name()
    directory.mkdir(parents=True)
    (directory / ("core-worker.exe" if os.name == "nt" else "core-worker")).write_bytes(
        b"changed executable"
    )
    (directory / "worker.toml").write_text(
        f'frida = "17.23.3"\ntarget = "{target_name()}"\nsha256 = "{"0" * 64}"\n',
        encoding="utf-8",
    )
    with pytest.raises(WorkerError, match="hash"):
        verify_worker(tmp_path)


@pytest.mark.embedded
def test_controller_reports_cleanup_failure_if_worker_is_lost(
    worker_executable, embedded_project, tmp_path
):
    # A distinct fixture path prevents the controller from matching pytest itself.
    runtime = tmp_path / "owned game"
    shutil.copytree(embedded_project / "libs/python-3.13.16-embed-amd64", runtime)
    backend = Backend(worker_executable, output=lambda *args: None)
    messages = []
    controller = Controller(
        backend.get_local_device(),
        [str(runtime / "python.exe"), "-B", str(TARGET), str(tmp_path / "released")],
        messages.append,
    )
    try:
        controller.start()
        # Simulate losing the infrastructure helper, never the target process.
        backend.worker.process.kill()
        backend.worker.process.wait(timeout=5)
        backend.worker.reader.join(timeout=5)
        with pytest.raises(LaunchError, match="device was lost"):
            controller.poll()
    finally:
        controller.close()
        with pytest.raises(WorkerError, match="exited with code"):
            backend.close()
    assert controller.cleanup_failed
    assert messages[-1] == "CONTROLLER STOPPED; cleanup incomplete; see errors above"
