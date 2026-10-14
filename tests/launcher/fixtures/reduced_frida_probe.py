# SPDX-License-Identifier: MPL-2.0
"""Use the built binding on owned Python processes to check local injection features."""

import importlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

# -I -S keeps global/user packages out; add only the checked wheel's extracted folder.
package = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(package))
frida = importlib.import_module("frida")
assert Path(frida.__file__).resolve().is_relative_to(package)
assert frida.__version__ == sys.argv[2], frida.__version__
device = frida.get_local_device()
children = queue.Queue()
stdout = []


def child_added(child):
    """Queue the one-argument Frida event; Queue.put itself has extra optional arguments."""
    children.put(child)


device.on("child-added", child_added)


def output(pid, fd, data):
    """Capture process stdout separately from script messages."""
    if fd == 1:
        stdout.append(data)


device.on("output", output)
sessions = []
owned = set()
capabilities = []
rpc_results = []
origins = []


def attach(pid):
    """Load QuickJS and enable gating before an owned target can create children."""
    session = device.attach(pid)
    sessions.append(session)
    session.enable_child_gating()
    ready = threading.Event()

    def message(value, data):
        assert value["type"] == "send", value
        capabilities.append(value["payload"])
        ready.set()

    script = session.create_script(
        # Replace an owned C function and restore it; checking the API's name alone cannot prove hooking works.
        "const module = new CModule('int answer(void) { return 7; }'); "
        "const answer = new NativeFunction(module.answer, 'int', []); "
        "const replacement = new NativeCallback(() => 42, 'int', []); "
        "Interceptor.replace(module.answer, replacement); Interceptor.flush(); "
        "const replaced = answer() === 42; "
        "Interceptor.revert(module.answer); Interceptor.flush(); "
        "send({native_hook: replaced && answer() === 7, "
        "runtime: Script.runtime, database: typeof SqliteDatabase, "
        "native: typeof NativeFunction, interceptor: typeof Interceptor}); "
        "rpc.exports = {add(a, b) { return a + b; }};",
        runtime="qjs",
    )
    script.on("message", message)
    script.load()
    assert ready.wait(5), "QuickJS did not deliver a message"
    rpc_results.append(script.exports_sync.add(7, 8))
    return session


root_pid = device.spawn(
    # Use the real base executable instead of Windows' venv redirector.
    [
        sys._base_executable,
        "-I",
        "-S",
        "-B",
        str(Path(__file__).with_name("reduced_frida_target.py")),
    ],
    stdio="pipe",
)
owned.add(root_pid)
resumed = False
try:
    root_session = attach(root_pid)
    disabled_v8 = False
    try:
        root_session.create_script("send('unexpected V8');", runtime="v8")
    except frida.NotSupportedError:
        disabled_v8 = True
    assert disabled_v8, "V8 was not removed"
    device.resume(root_pid)
    resumed = True
    deadline = time.monotonic() + 15
    while not root_session.is_detached or not children.empty():
        assert time.monotonic() < deadline, "Owned helper tree did not finish"
        try:
            child = children.get(timeout=0.05)
        except queue.Empty:
            continue
        # Only attach to children emitted for this owned, gated process tree.
        assert child.parent_pid in owned, child
        owned.add(child.pid)
        origins.append(child.origin)
        attach(child.pid)
        device.resume(child.pid)
    # POSIX emits separate fork and exec events, including a new session for the same PID.
    expected_origins = ["fork", "exec"] if os.name == "posix" else ["spawn"]
    assert origins == expected_origins, origins
    assert len(sessions) == len(expected_origins) + 1
    assert capabilities == [
        {
            "native_hook": True,
            "runtime": "QJS",
            "database": "undefined",
            "native": "function",
            "interceptor": "object",
        }
    ] * len(sessions)
    assert rpc_results == [15] * len(sessions)
    # Output notifications can arrive just after the detach notification.
    while (
        not all(
            marker in b"".join(stdout)
            for marker in (b"owned parent output", b"owned child output")
        )
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    captured = b"".join(stdout)
    assert (
        b"owned parent output" in captured and b"owned child output" in captured
    ), captured
    print(
        json.dumps(
            {
                "quickjs": True,
                "native_hook": True,
                "sqlite_absent": True,
                "v8_disabled": True,
                "child_gating": True,
                "script_rpc": True,
                "stdio": True,
            }
        )
    )
finally:
    # Release gated children through their sessions; helpers finish naturally after detach.
    # Avoid PID-based kills, which could target a recycled PID after a helper has exited.
    for session in sessions:
        try:
            if not session.is_detached:
                session.disable_child_gating()
                session.detach()
        except (frida.InvalidOperationError, frida.TransportError):
            # A fork/exec or process exit can close the session after the status check.
            pass
    if not resumed:
        try:
            device.resume(root_pid)
        except frida.ProcessNotFoundError:
            pass
