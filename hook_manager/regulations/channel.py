# SPDX-License-Identifier: MPL-2.0
"""Trusted synchronous regulation calls over one authenticated persistent socket."""

import math
import socket
import threading
import time

if __package__:
    from hook_manager.manager import HookError
    from .framing import regulation_decisions, regulation_read, regulation_write


class RegulationChannel:
    """Correlate replies to this session and never retry an expired action.

    The endpoint comes only from the launcher. The lock rejects nested/concurrent
    access before it touches the stream; native operation admission stays in the
    generic callable engine and is not delegated to this transport.
    """

    def __init__(self, endpoint):
        self._connection = None
        self._lock = threading.Lock()
        self._sequence, self.closed = 0, False
        self._unavailable = set()
        try:
            if (type(endpoint) is not dict or set(endpoint) != {"port", "token"}
                    or type(endpoint["port"]) is not int or not 1 <= endpoint["port"] <= 65535
                    or type(endpoint["token"]) is not str or len(endpoint["token"]) != 64
                    or any(char not in "0123456789abcdef" for char in endpoint["token"])):
                raise HookError("Invalid trusted regulation endpoint")
            self._token = endpoint["token"]
            deadline = time.monotonic() + 1
            self._connection = socket.create_connection(("127.0.0.1", endpoint["port"]), timeout=1)
            self._connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._connection.setblocking(False)
            regulation_write(self._connection, {"token": self._token, "id": 0}, deadline)
            if regulation_read(self._connection, deadline) != {"token": self._token, "id": 0, "bound": True}:
                raise HookError("Regulation endpoint did not authenticate")
        except Exception:
            self.close()
            raise

    def __call__(self, owner, calls, deadline):
        if type(owner) is str and owner in self._unavailable:
            raise HookError("Required regulation owner is unavailable")
        if self.closed or not self._lock.acquire(blocking=False):
            raise HookError("Regulation channel is unavailable or already busy")
        failure = None
        try:
            if (type(owner) is not str or not owner or type(calls) is not list or not 1 <= len(calls) <= 32
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)):
                raise HookError("Invalid bounded regulation request")
            deadline = min(deadline, time.monotonic() + 0.1)
            self._sequence += 1
            regulation_write(self._connection, {"token": self._token, "id": self._sequence,
                                               "owner": owner, "calls": calls, "deadline": deadline}, deadline)
            reply = regulation_read(self._connection, deadline)
            if (type(reply) is not dict or reply.get("token") != self._token
                    or type(reply.get("id")) is not int or reply["id"] != self._sequence
                    or reply.get("owner") != owner):
                raise HookError("Stale or foreign regulation reply")
            if set(reply) == {"token", "id", "owner", "error"}:
                if reply["error"] != "Validation unavailable":
                    raise HookError("Invalid regulation error reply")
                # An identified owner's failure must not close the channel used
                # by other owners. Only protocol/disconnect/timeout faults close it.
                failure = reply["error"]
            elif (set(reply) != {"token", "id", "owner", "decisions"}
                  or type(reply["decisions"]) is not list or len(reply["decisions"]) != len(calls)):
                raise HookError("Incomplete regulation reply")
            else:
                try:
                    regulation_decisions(reply["decisions"], len(calls))
                except ValueError:
                    failure = "Validation unavailable"
            if time.monotonic() >= deadline:
                raise HookError("Late regulation reply")
        except Exception:
            self.close()
            raise
        finally:
            self._lock.release()
        if failure:
            self._unavailable.add(owner)
            raise HookError(failure)
        return reply["decisions"]

    def close(self):
        self.closed = True
        if self._connection is not None:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._connection.close()
            self._connection = None
