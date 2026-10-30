# SPDX-License-Identifier: MPL-2.0
"""Trusted fixed policy operations; never a mod-facing callable resolver."""

import socket
import threading
import time

from ...manager import HookError
from .framing import regulation_decisions, regulation_read, regulation_write
from .protocol import regulation_header, regulation_integer, regulation_pages, regulation_policy_value, regulation_votes


class RegulationChannel:
    def __init__(self, endpoint):
        self._connection, self.closed = None, False
        # Direct trusted users retain their explicit acknowledgement path.
        # Prepared career setup closes this gate before installing any hooks.
        self.validation_ready = True
        self._lock, self._sequence, self._action = threading.Lock(), 0, 0
        # Publication IDs increase across careers; each career installs once.
        # The explicit flag also covers switch-only packs with no owner tables.
        self._unavailable, self._generations, self._generation = set(), {}, 0
        self._published = False
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
            hello = {"version": 2, "op": "hello", "token": self._token, "id": 0}
            regulation_write(self._connection, hello, deadline)
            reply = regulation_read(self._connection, deadline)
            if (type(reply) is not dict or set(reply) != set(hello) | {"epoch", "owners", "custom"}
                    or any(type(reply[key]) is not type(value) or reply[key] != value for key, value in hello.items())):
                raise HookError("Regulation endpoint did not authenticate")
            self.epoch = regulation_integer(reply["epoch"], positive=True)
            # Hello supplies admitted identities/subscriptions, not installed
            # policy. _generations stays empty until publication is acknowledged.
            self._owners, self._custom = reply["owners"], frozenset(reply["custom"])
            self._rules = {rule for row in self._owners.values() for rule in row["rules"]}
            self.voting = {}
            for owner, row in self._owners.items():
                rules = row.get("voting", [])
                if type(rules) is not list or len(rules) > 32:
                    raise HookError("Invalid voting subscriptions")
                for rule in rules:
                    if type(rule) is not str or not 1 <= len(rule) <= 256 or rule in self.voting:
                        raise HookError("Conflicting or invalid voting subscription")
                    self.voting[rule] = owner
        except Exception:
            self.close()
            raise

    def bindings(self, scope):
        """Return admitted binding slots for a trusted scope, never native targets.

        Slots follow the launcher's ascending validator IDs. A single binding
        per owner/target can therefore fan out to every applicable mod handler.
        """
        result = {}
        for owner, row in sorted(self._owners.items()):
            bindings = row.get("bindings")
            if type(bindings) is not list or not 0 <= len(bindings) <= 32 or not (bindings or row.get("voting")):
                raise HookError("Missing admitted regulation bindings")
            selected = []
            for slot, binding in enumerate(bindings):
                if (type(binding) is not dict or set(binding) != {"rule", "scope"}
                        or binding["rule"] not in row["rules"] or type(binding["scope"]) is not str):
                    raise HookError("Invalid admitted regulation binding")
                if binding["scope"] == scope:
                    # Keep the original slot: other scopes can leave gaps, and
                    # renumbering would call a different admitted handler.
                    selected.append((slot, binding["rule"]))
            if selected:
                result[owner] = tuple(selected)
        return result

    def _enter(self):
        # One logical operation owns the stream through all policy pages.
        # Policy phases share this request ID rather than reserving new IDs.
        if self.closed or not self._lock.acquire(blocking=False):
            raise HookError("Regulation channel is unavailable or already busy")
        self._sequence = regulation_integer(self._sequence + 1, positive=True)

    def _message(self, op, epoch, generations, deadline, **body):
        return {"version": 2, "token": self._token, "id": self._sequence, "op": op,
                "epoch": epoch, "generations": generations, "deadline": deadline, **body}

    def _exchange(self, message):
        expected = regulation_header(message)
        regulation_write(self._connection, message, message["deadline"])
        try:
            reply = regulation_read(self._connection, message["deadline"])
            actual = regulation_header(reply, reply=True)
        except ValueError as error:
            raise HookError("Invalid regulation reply") from error
        if actual.keys() != expected.keys() or any(type(actual[key]) is not type(value) or actual[key] != value for key, value in expected.items()):
            raise HookError("Stale or foreign regulation reply")
        if "error" in reply:
            failed = reply["unavailable"]
            if (reply["error"] != "Validation unavailable" or type(failed) is not list or not failed
                    or any(type(owner) is not str or owner not in self._owners for owner in failed)):
                raise HookError("Invalid regulation failure identity")
            self._unavailable.update(failed)
            # An identified failure is distinct from a broken stream. The action
            # caller keeps other owners usable; publish/reset treat it as fatal.
            return None
        return reply["result"]

    def publish(self, *, native=None, custom=None):
        """Install every subscribed rule once; reset before loading another career."""
        self._enter()
        try:
            if self._published:
                raise HookError("Regulation policy is already installed; reset for a new career")
            native, custom = {} if native is None else native, {} if custom is None else custom
            if type(native) is not dict or type(custom) is not dict or native.keys() & custom.keys():
                raise HookError("Invalid regulation publication")
            keys = native.keys() | custom.keys()
            if keys != self._rules or native.keys() & self._custom or not custom.keys() <= self._custom:
                raise HookError("Foreign regulation publication")
            affected = self._owners.keys()
            if affected & self._unavailable:
                raise HookError("Required regulation owner is unavailable")
            generation = regulation_integer(self._generation + 1, positive=True)
            generations = dict.fromkeys(sorted(affected), generation)
            deadline = time.monotonic() + 1 + 5 * len(affected)
            # Preflight all finite choices before sending any pages. Declared
            # byte/node totals let the receiver verify completeness as it streams.
            plan = []
            for kind, mapping in (("native", native), ("custom", custom)):
                for rule, values in mapping.items():
                    if type(values) is not list or not values:
                        raise HookError("Expected complete reachable regulation choices")
                    size = nodes = 0
                    for value in values:
                        _, record_bytes, record_nodes = regulation_policy_value(kind, value)
                        size += record_bytes
                        nodes += record_nodes
                    plan.append([kind, rule, len(values), size, nodes])
            result = self._exchange(self._message("policy-begin", self.epoch, generations, deadline, plan=plan))
            if result is not None:
                if result != "staging":
                    raise HookError("Invalid policy begin acknowledgement")
                rows = ([rule, index, regulation_policy_value(kind, value)[0]]
                        for kind, mapping in (("native", native), ("custom", custom)) for rule, values in mapping.items()
                        for index, value in enumerate(values))
                # Wait for each page acknowledgement before producing the next.
                # This bounds queued data without buffering one large JSON policy.
                for index, page in enumerate(regulation_pages(rows)):
                    result = self._exchange(self._message("policy-page", self.epoch, generations, deadline, page=index, rows=page))
                    if result is None:
                        break
                    if result != "staging":
                        raise HookError("Invalid policy page acknowledgement")
                if result is not None:
                    result = self._exchange(self._message("policy-seal", self.epoch, generations, deadline))
            if result is None:
                raise HookError("Validation unavailable")
            if result != "active":
                raise HookError("Invalid policy commit acknowledgement")
            # Keep pending generations out of action admission until every guest
            # commits and the matching native acknowledgement arrives.
            self._generations = generations
            self._published = True
            self._generation = generation
        except Exception:
            self.close()
            raise
        finally:
            self._lock.release()

    def reset(self, epoch):
        self._enter()
        try:
            regulation_integer(epoch, positive=True)
            if epoch <= self.epoch or self._unavailable:
                raise HookError("Career reset cannot revive regulation owners")
            deadline = time.monotonic() + 1 + 5 * len(self._owners)
            generations = {owner: self._generations.get(owner, 0) for owner in self._owners}
            result = self._exchange(self._message("reset", epoch, generations, deadline))
            if result != "reset":
                raise HookError("Validation unavailable")
            # Career data is gone, so no owner can act until republished. Keep
            # session publication/action counters increasing to reject old IDs.
            self.epoch, self._generations, self._published = epoch, {}, False
        except Exception:
            self.close()
            raise
        finally:
            self._lock.release()

    def __call__(self, contexts, selections, calls, deadline):
        """Return checked decisions for every requested handler, or raise.

        Calls map each owner to [binding slot, shared context, shared selection]
        rows. All owner identities, decision counts and contents are checked
        within the deadline before any result is returned.
        """
        # Reject an already failed owner before touching the stream, allowing
        # other owners to keep using this same authenticated connection.
        if type(calls) is dict and calls.keys() & self._unavailable:
            raise HookError("Required regulation owner is unavailable")
        self._enter()
        try:
            if type(calls) is not dict or not calls or not calls.keys() <= self._generations.keys():
                raise HookError("Required regulation owner is unavailable")
            self._action = regulation_integer(self._action + 1, positive=True)
            generations = {owner: self._generations[owner] for owner in calls}
            owners = {owner: {"revision": self._owners[owner]["revision"], "calls": rows} for owner, rows in calls.items()}
            result = self._exchange(self._message("action", self.epoch, generations, deadline, action=self._action,
                                                 contexts=contexts, selections=selections, owners=owners))
            if result is not None:
                if type(result) is not dict or result.keys() != calls.keys():
                    raise HookError("Incomplete regulation owners")
                for owner, decisions in result.items():
                    regulation_decisions(decisions, len(calls[owner]))
                # Validating a large refusal result is still action work. An
                # on-time header alone cannot authorize a late completed result.
                if time.monotonic() >= deadline:
                    raise HookError("Late regulation reply")
        except Exception:
            self.close()
            raise
        finally:
            self._lock.release()
        if result is None:
            raise HookError("Validation unavailable")
        return result

    def close(self):
        self.closed = True
        if self._connection is not None:
            try:
                self._connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._connection.close()
            self._connection = None

    def vote(self, snapshot, regulation, contexts, *, deadline=None):
        """Use this career's admitted owner and original deadline for one proposal."""
        owner = self.voting[regulation["key"]]
        if not self.validation_ready or owner in self._unavailable or owner not in self._generations:
            raise HookError("Required voting owner is unavailable")
        self._enter()
        try:
            deadline = time.monotonic() + 5 if deadline is None else min(time.monotonic() + 5, deadline)
            result = self._exchange(self._message("vote", self.epoch, {owner: self._generations[owner]}, deadline,
                                                owner=owner, snapshot=snapshot, regulation=regulation, contexts=contexts))
            if not self.validation_ready or self.closed:
                raise HookError("Voting career is no longer active")
            result = regulation_votes(result, [row["team"] for row in contexts])
            if time.monotonic() >= deadline:
                raise HookError("Voting reply validation exceeded its deadline")
            return result
        except Exception as error:
            self.close()
            raise HookError("Voting unavailable") from error
        finally:
            self._lock.release()
