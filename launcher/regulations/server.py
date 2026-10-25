# SPDX-License-Identifier: MPL-2.0
"""Own transport I/O without ever entering a guest from the listener thread."""

import queue
import secrets
import socket
import threading
import time

from hook_manager.game_adapters.regulations.framing import regulation_read, regulation_write
from hook_manager.game_adapters.regulations.protocol import regulation_header
from .transport_request import TransportRequest


class RegulationServer:
    """Queue one authenticated request and wake the owning controller.

    Only poll() may call the supplied evaluator. Disconnect/timeout is terminal
    after authentication; a new connection cannot silently revive an old session.
    Unauthenticated connections receive no owner data and cannot latch owners.
    """

    def __init__(self, metadata, wake=None):
        self._metadata, self._wake = metadata, wake
        self._thread_id = threading.get_ident()
        # A sender waits for the controller's acknowledgement before the next
        # page/operation. The queue holds one request, not an accumulating backlog.
        self._requests = queue.Queue(maxsize=1)
        self._stop, self.failed, self.connected = threading.Event(), threading.Event(), threading.Event()
        self._connection, self._pending_request = None, None
        self._listener = socket.socket()
        self._interrupt_read = self._interrupt_write = None
        try:
            self._listener.bind(("127.0.0.1", 0))
            self._listener.listen(1)
            self._listener.settimeout(0.05)
            # Windows need not wake a local select() when its socket shuts down
            # from another thread. A readable peer makes idle/partial reads stop
            # immediately, without adding a periodic idle polling loop.
            self._interrupt_read, self._interrupt_write = socket.socketpair()
            self._interrupt_write.setblocking(False)
            self._token = secrets.token_hex(32)
            self._thread = threading.Thread(target=self._serve, name="regulation-transport", daemon=True)
            self._thread.start()
        except BaseException:
            self._listener.close()
            for connection in (self._interrupt_read, self._interrupt_write):
                if connection is not None:
                    connection.close()
            raise

    @property
    def endpoint(self):
        return {"port": self._listener.getsockname()[1], "token": self._token}

    def _serve(self):
        authenticated = False
        try:
            while not self._stop.is_set():
                try:
                    connection, _ = self._listener.accept()
                except socket.timeout:
                    continue
                self._connection = connection
                try:
                    # close() may have missed this socket while accept() returned.
                    # Publish it before checking stop so either side closes it.
                    if self._stop.is_set():
                        return
                    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    connection.setblocking(False)
                    hello = regulation_read(connection, time.monotonic() + 1, interrupt=self._interrupt_read)
                    if (type(hello) is not dict or set(hello) != {"version", "op", "token", "id"}
                            or type(hello["version"]) is not int or hello["version"] != 2 or hello["op"] != "hello"
                            or type(hello["id"]) is not int or hello["id"] != 0
                            or type(hello["token"]) is not str or len(hello["token"]) != 64
                            or any(char not in "0123456789abcdef" for char in hello["token"])
                            or not secrets.compare_digest(hello["token"], self._token)):
                        continue
                    # Before authentication a bad connection may be discarded.
                    # Afterwards this is the one session: faults are terminal,
                    # so reconnecting cannot restore an earlier ready policy.
                    authenticated = True
                    self.connected.set()
                    regulation_write(connection, {**hello, **self._metadata}, time.monotonic() + 1)
                    sequence, pending = 0, None
                    while not self._stop.is_set():
                        message = regulation_read(connection, None if pending is None else pending["deadline"],
                                                  interrupt=self._interrupt_read)
                        header = regulation_header(message)
                        if not secrets.compare_digest(message["token"], self._token):
                            raise ValueError("Foreign regulation session")
                        # Continuations retain the initial identity and deadline;
                        # a new operation cannot interleave with a partial policy.
                        continuation = message["op"] in ("policy-page", "policy-seal")
                        if continuation:
                            if pending is None or any(message[key] != value for key, value in pending.items()):
                                raise ValueError("Foreign regulation publication phase")
                        elif pending is not None or message["id"] != sequence + 1:
                            raise ValueError("Invalid correlated regulation request")
                        if not continuation:
                            sequence = message["id"]
                        request = TransportRequest(message)
                        # Publish before checking stop, as for accepted sockets:
                        # close() must release even a long control-event wait.
                        self._pending_request = request
                        if self._stop.is_set():
                            return
                        self._requests.put_nowait(request)
                        if self._wake is not None:
                            self._wake()
                        if not request.done.wait(max(0, message["deadline"] - time.monotonic())):
                            raise TimeoutError("Regulation controller missed the action deadline")
                        if self._stop.is_set():
                            return
                        regulation_write(connection, request.response, message["deadline"])
                        if message["op"] == "policy-begin" and "error" not in request.response:
                            pending = {key: header[key] for key in ("id", "epoch", "deadline", "generations")}
                        elif message["op"] == "policy-seal" or "error" in request.response:
                            pending = None
                        # The next read can wait indefinitely. Retain only small
                        # correlation state, never the completed action's graph.
                        self._pending_request = None
                        del request, message
                except (ValueError, OSError, RuntimeError, queue.Full):
                    if authenticated:
                        raise
                finally:
                    connection.close()
                    self._connection = None
                if authenticated:
                    break
        except Exception:
            if not self._stop.is_set():
                self.failed.set()
                if self._wake is not None:
                    self._wake()
        finally:
            self._pending_request = None
            self.connected.clear()

    def poll(self, handle, unavailable=None):
        if threading.get_ident() != self._thread_id:
            raise RuntimeError("Use regulation transport on its owning controller thread")
        try:
            request = self._requests.get_nowait()
        except queue.Empty:
            return
        message = request.message
        # Preserve identity even if the request expired while queued. The check
        # below then builds a correlated error without invoking its evaluator.
        response = regulation_header(message, check_deadline=False)
        try:
            if self._stop.is_set() or self.failed.is_set() or time.monotonic() >= message["deadline"]:
                raise TimeoutError("Expired regulation request")
            response["result"] = handle(message)
            if time.monotonic() >= message["deadline"]:
                raise TimeoutError("Late regulation evaluation")
        except Exception:
            response.pop("result", None)
            response["error"] = "Validation unavailable"
            response["unavailable"] = unavailable() if unavailable is not None else sorted(message["generations"])
        finally:
            request.response = response
            # Release socket I/O before PackMods flushes native UI changes. The
            # native caller may be waiting for this reply before it can serve UI.
            request.done.set()

    def close(self):
        self._stop.set()
        try:
            self._interrupt_write.send(b"x")
        except OSError:
            pass
        self._listener.close()
        request = self._pending_request
        if request is not None:
            # Socket shutdown cannot wake an Event wait. Release it explicitly
            # so a long policy deadline cannot hold pack shutdown open.
            request.done.set()
        connection = self._connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            raise RuntimeError("Regulation transport did not stop")
        self._interrupt_read.close()
        self._interrupt_write.close()
        try:
            self._requests.get_nowait()  # Retire a queued operation never polled.
        except queue.Empty:
            pass
