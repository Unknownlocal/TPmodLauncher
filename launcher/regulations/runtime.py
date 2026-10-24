# SPDX-License-Identifier: MPL-2.0
"""Controller-owned binding, policy publication and pack readiness."""

import threading

from launcher.shared_source_mod import SharedSourceMod
from mod_runtime._regulations.values import integer
from .shared_handler_runner import SharedHandlerRunner
from .publication import PolicyPublication
from .server import RegulationServer
from .validation.dispatch import ValidationDispatch


# These are complete native scopes, not mod-selected function names. A new
# adapter family needs an explicit mapping before its validators can bind.
SCOPE_COMPONENTS = {
    "personnel.contract": frozenset({"driver_signing", "staff_signing", "academy_signing", "staffing"}),
    "development.project": frozenset({"development_projects"}),
}


class RegulationRuntime:
    def __init__(self, definitions, mods, wake=None):
        self.definitions, self._wake = definitions, wake
        # Pack cleanup removes failed members from its live map. Retain required
        # identities, including owners that declare data without a validator.
        self._mods = {owner: mods.get(owner) for owner in definitions.owner_ids}
        self._thread = threading.get_ident()
        # Keep every admitted owner here even after failure: closing a Store
        # must make required validation unavailable, not silently disable a rule.
        self._states = dict.fromkeys(definitions.owner_ids, "pending")
        self._handlers, self._server, self._dispatch = {}, None, None
        self._publication, self._action = None, 0
        self._native = self.closed = self._attempted = self._bound = False
        self._activation_pending = False
        self._required_components = set()

    def _owning_thread(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError("Use regulation runtime on its owning controller thread")

    def bind(self):
        self._owning_thread()
        if self.closed or self._attempted:
            raise RuntimeError("Regulations must bind once in their pack session")
        self._attempted = True
        groups = {}
        try:
            # Admission can describe bridges before their execution support is
            # installed. Never treat them as harmless definition-only owners.
            if self.definitions.native_rules:
                raise ValueError("Native regulation rule bridges are not available for binding")
            validators_by_owner = self.definitions.validators
            for validators in validators_by_owner.values():
                for validator in validators:
                    scope = validator["scope"]
                    if scope not in SCOPE_COMPONENTS:
                        raise ValueError("No complete native regulation mapping for scope: " + scope)
                    self._required_components.update(SCOPE_COMPONENTS[scope])
            for owner, validators in validators_by_owner.items():
                mod = self._mods[owner]
                if (type(mod) is not SharedSourceMod or not mod._active
                        or mod.sandbox.closed or mod.manifest.id != owner):
                    raise ValueError("Regulation source owner is no longer active")
                if validators:
                    groups.setdefault(mod.runner, {})[owner] = validators
            # Bind once per physical runner. Empty tables need no guest resident,
            # but their owners still participate in readiness and group failure.
            for runner, tables in groups.items():
                handler = SharedHandlerRunner.bind(runner, tables)
                self._handlers.update(dict.fromkeys(tables, handler))
            self._states = dict.fromkeys(self._states, "bound")
        except Exception:
            self._unavailable(self._states)
            raise
        # Definition-only owners remain required by the pack, but need no guest
        # execution view. The dispatcher claims only owners with bound validators.
        self._dispatch = ValidationDispatch(self.definitions, self._handlers)
        if self._states:
            catalog = self._dispatch.catalog
            # Preserve the catalog's ascending validator-ID order as wire slots.
            # Native adapters receive subscriptions, never handler Python code.
            metadata = {"epoch": self._dispatch.epoch, "custom": sorted(catalog.custom),
                        "owners": {owner: {"revision": revision, "rules": sorted({row["rule"] for row in validators}),
                                           "bindings": [{key: row[key] for key in ("rule", "scope")} for row in validators]}
                                   for owner, (revision, validators, _) in catalog.owners.items()}}
            self._server = RegulationServer(metadata, self._wake)
        self._bound = True

    @property
    def endpoint(self):
        if self.closed or self._server is None:
            raise RuntimeError("Regulation transport is not bound")
        return self._server.endpoint

    @property
    def ready(self):
        if not self._bound or self.closed:
            return False
        if not self._states:
            return True
        # Bound definition-only owners need no execution policy. For validator
        # owners, dispatch.ready separately requires an active committed view.
        # Neither native guard acknowledgement nor a socket handshake proves it.
        return (self._native and not self._activation_pending and self._publication is None and self._dispatch.ready
                and self._server.connected.is_set() and not self._server.failed.is_set()
                and all(state in ("bound", "active") and not self._mods[owner].sandbox.closed
                        for owner, state in self._states.items()))

    @property
    def status(self):
        return {"ready": self.ready, "owners": dict(self._states)}

    def confirm_native(self, components, *, pending=False):
        """Accept installation coverage for every scope used by this pack.

        Trusted setup supplies the AdapterModules installation report. Signing
        alone cannot cover personnel staffing operations. Other components in
        the report are irrelevant to this pack; policy publication still has
        its own readiness check. Source mods cannot call this acknowledgement.
        With pending=True, coverage is approved but pack readiness stays held
        until the game replies that its installed hooks have been enabled.
        """
        self._owning_thread()
        # A rejected replacement report must not retain a previous approval.
        self._native = False
        if (self.closed or not self._bound or any(state == "unavailable" for state in self._states.values())
                or (self._states and (not self._server.connected.is_set() or self._server.failed.is_set()))):
            raise RuntimeError("Incomplete regulation binding cannot become ready")
        if type(components) is not dict:
            raise ValueError("Native regulation acknowledgement needs a component report")
        missing = sorted(key for key in self._required_components if components.get(key) is not True)
        if missing:
            raise ValueError("Incomplete native regulation coverage: " + ", ".join(missing))
        if pending and not self._dispatch.ready:
            raise RuntimeError("Native activation needs committed regulation data")
        self._activation_pending = pending
        self._native = True

    def finish_native_activation(self):
        """Release pack readiness only after the game confirms its hooks enabled."""
        self._owning_thread()
        if not self._native or not self._activation_pending:
            raise RuntimeError("No native regulation activation is pending")
        self._activation_pending = False
        if not self.ready:
            self._native = False
            raise RuntimeError("Native regulation activation became unavailable")

    def _unavailable(self, owners):
        # A shared Store can fail through a non-regulation peer too. Expand any
        # affected owner to every required owner of that same physical runner.
        runners = {self._mods[owner].runner for owner in owners
                   if type(self._mods[owner]) is SharedSourceMod}
        for owner, mod in self._mods.items():
            if type(mod) is not SharedSourceMod or mod.runner in runners:
                self._states[owner] = "unavailable"
        for runner in runners:
            runner.close()

    def _closed_owners(self):
        for owner, mod in self._mods.items():
            if self._server.failed.is_set() or mod is None or mod.sandbox.closed:
                yield owner

    def handle(self, message):
        """Only poll() enters this on the Store-owning thread, never socket I/O."""
        self._owning_thread()
        # Before resolving a valid operation, failure can identify only known
        # requested owners. Policy resolution below replaces this with the
        # catalog-derived affected set; execution narrows it to failed Stores.
        affected = set(message["generations"]) & self._states.keys()
        try:
            operation = message["op"]
            if self.closed:
                raise RuntimeError("Regulation runtime is closed")
            # Setup publishes rule data before installing any live game hooks.
            # Only data publication can precede coverage acknowledgement; an
            # action or career reset still needs acknowledged native ownership.
            if not self._native and operation not in ("policy-begin", "policy-page", "policy-seal"):
                raise RuntimeError("Native regulation guards are not acknowledged")
            if operation != "reset" and message["epoch"] != self._dispatch.epoch:
                raise ValueError("Foreign regulation career epoch")
            if operation == "policy-begin":
                if self._publication is not None:
                    raise ValueError("Regulation publication already pending")
                candidate = PolicyPublication(self._dispatch, message)
                affected = candidate.affected
                if any(self._states[owner] == "unavailable" or self._mods[owner].sandbox.closed for owner in affected):
                    raise RuntimeError("Required regulation owner is unavailable")
                self._publication = candidate
                # Fence the old policy before receiving its replacement pages,
                # not merely when guest installation eventually starts at seal.
                for owner in affected:
                    self._states[owner] = "dirty"
                return "staging"
            if operation in ("policy-page", "policy-seal"):
                if self._publication is None:
                    raise ValueError("No regulation publication is pending")
                affected = self._publication.affected
                if operation == "policy-page":
                    self._publication.append(message)
                    return "staging"
                # Seal returns only after the complete policy is installed and
                # committed in all affected Stores. Page receipt alone never
                # changes owner readiness to active.
                self._publication.seal(self._dispatch, message)
                for owner in affected:
                    self._states[owner] = "active"
                self._publication = None
                return "active"
            if self._publication is not None:
                raise ValueError("Regulation publication has fenced actions")
            if operation == "reset":
                # Reset retires the entire career, not just owners named by an
                # action. Its generation map must describe all execution views.
                affected = set(self._states)
                expected = {owner: runner.generation for owner, runner in self._dispatch.runners.items()}
                if message["generations"] != expected:
                    raise ValueError("Foreign career reset generations")
                self._dispatch.reset(message["epoch"], deadline=message["deadline"])
                for owner in self._dispatch.runners:
                    self._states[owner] = "bound"
                return "reset"
            if operation != "action":
                raise ValueError("Unknown regulation operation")
            if integer(message["action"], 64, positive=True) <= self._action:
                raise ValueError("Stale regulation action")
            self._action = message["action"]
            owners = message["owners"]
            if type(owners) is not dict or not owners or owners.keys() != message["generations"].keys():
                raise ValueError("Invalid regulation action owners")
            calls = {}
            for owner, row in owners.items():
                view = self._dispatch.catalog.views[owner]
                if (self._states[owner] != "active" or type(row) is not dict or set(row) != {"revision", "calls"}
                        or row["revision"] != view.revision or message["generations"][owner] != self._dispatch.runners[owner].generation
                        or type(row["calls"]) is not list or not 1 <= len(row["calls"]) <= 32):
                    raise ValueError("Stale or foreign regulation owner view")
                # Wire slots resolve only against admitted ascending IDs. They
                # cannot supply a Python function, native target or new binding.
                bindings = tuple(view.validators)
                calls[owner] = []
                for call in row["calls"]:
                    if type(call) is not list or len(call) != 3 or integer(call[0]) >= len(bindings):
                        raise ValueError("Invalid regulation binding reference")
                    calls[owner].append([bindings[call[0]], call[1], call[2]])
            try:
                return self._dispatch.evaluate(message["contexts"], message["selections"], calls, deadline=message["deadline"])
            finally:
                # Dispatch distinguishes a preflight cohort failure from a single
                # executing owner. Preserve that distinction on the socket.
                affected = {owner for owner, state in self._dispatch.states.items() if state == "unavailable"}
        except Exception:
            self._unavailable(affected)
            self._publication = None
            raise

    def poll(self):
        self._owning_thread()
        if self.closed or self._server is None:
            return
        self._unavailable(tuple(self._closed_owners()))
        # The native channel knows execution owners only. Definition-only owners
        # remain in pack status, but cannot appear as foreign binding identities.
        self._server.poll(self.handle, lambda: sorted(owner for owner in self._dispatch.runners
                                                      if self._states[owner] == "unavailable"))
        if self._server.failed.is_set():
            self._publication = None  # Release partially received policy on terminal I/O failure.
        self._unavailable(tuple(self._closed_owners()))

    def close(self):
        self._owning_thread()
        if self.closed:
            return
        self.closed, self._native = True, False
        self._publication = None
        if self._server is not None:
            self._server.close()
        # PackMods closes its shared/independent Stores after socket I/O stops;
        # transport never races guest teardown.
