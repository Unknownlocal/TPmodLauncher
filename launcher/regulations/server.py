# SPDX-License-Identifier: MPL-2.0
"""Own transport I/O without ever entering a guest from the listener thread."""

import math
import queue
import secrets
import socket
import threading
import time

from hook_manager.regulations.framing import regulation_read, regulation_write
from .transport_request import TransportRequest


class RegulationServer:
    """Queue one authenticated request and wake the owning controller.

    Only poll() may call the supplied evaluator. Disconnect/timeout is terminal
    after authentication; a new connection cannot silently revive an old session.
    Unauthenticated connections receive no owner data and cannot latch owners.
    """

    def __init__(self, owners, wake=None):
        self._owners, self._wake = frozenset(owners), wake
        self._thread_id = threading.get_ident()
        self._requests = queue.Queue(maxsize=1)
        self._stop, self.failed, self.connected = threading.Event(), threading.Event(), threading.Event()
        self._connection = None
        self._listener = socket.socket()
        try:
            self._listener.bind(("127.0.0.1", 0))
            self._listener.listen(1)
            self._listener.settimeout(0.05)
            self._token = secrets.token_hex(32)
            self._thread = threading.Thread(target=self._serve, name="regulation-transport", daemon=True)
            self._thread.start()
        except BaseException:
            self._listener.close()
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
                    hello = regulation_read(connection, time.monotonic() + 1)
                    if (type(hello) is not dict or set(hello) != {"token", "id"}
                            or type(hello["id"]) is not int or hello["id"] != 0
                            or type(hello["token"]) is not str or len(hello["token"]) != 64
                            or any(char not in "0123456789abcdef" for char in hello["token"])
                            or not secrets.compare_digest(hello["token"], self._token)):
                        continue
                    authenticated = True
                    self.connected.set()
                    regulation_write(connection, {"token": self._token, "id": 0, "bound": True}, time.monotonic() + 1)
                    sequence = 0
                    while not self._stop.is_set():
                        message = regulation_read(connection, None)
                        if (type(message) is not dict or set(message) != {"token", "id", "owner", "calls", "deadline"}
                                or type(message["token"]) is not str or len(message["token"]) != 64
                                or any(char not in "0123456789abcdef" for char in message["token"])
                                or not secrets.compare_digest(message["token"], self._token)
                                or type(message["id"]) is not int or message["id"] != sequence + 1
                                or type(message["owner"]) is not str or message["owner"] not in self._owners
                                or type(message["calls"]) is not list or not 1 <= len(message["calls"]) <= 32
                                or type(message["deadline"]) not in (int, float) or not math.isfinite(message["deadline"])
                                or not 0 < message["deadline"] - time.monotonic() <= 0.1):
                            raise ValueError("Invalid correlated regulation request")
                        sequence = message["id"]
                        request = TransportRequest(message)
                        self._requests.put_nowait(request)
                        if self._wake is not None:
                            self._wake()
                        if not request.done.wait(max(0, message["deadline"] - time.monotonic())):
                            raise TimeoutError("Regulation controller missed the action deadline")
                        regulation_write(connection, request.response, message["deadline"])
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
            self.connected.clear()

    def poll(self, evaluate):
        if threading.get_ident() != self._thread_id:
            raise RuntimeError("Use regulation transport on its owning controller thread")
        try:
            request = self._requests.get_nowait()
        except queue.Empty:
            return
        message = request.message
        response = {key: message[key] for key in ("token", "id", "owner")}
        try:
            if self._stop.is_set() or self.failed.is_set() or time.monotonic() >= message["deadline"]:
                raise TimeoutError("Expired regulation request")
            response["decisions"] = evaluate(message["owner"], message["calls"], message["deadline"])
            if time.monotonic() >= message["deadline"]:
                raise TimeoutError("Late regulation evaluation")
        except Exception:
            response.pop("decisions", None)
            response["error"] = "Validation unavailable"
        finally:
            request.response = response
            request.done.set()

    def close(self):
        self._stop.set()
        self._listener.close()
        connection = self._connection
        if connection is not None:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            raise RuntimeError("Regulation transport did not stop")
