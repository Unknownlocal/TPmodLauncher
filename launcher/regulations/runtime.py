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
        self._handler, self._runner, self._server, self._dispatch = None, None, None, None
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
        try:
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
                if self._runner is None:
                    self._runner = mod.runner
                elif mod.runner is not self._runner:
                    raise ValueError("All regulation owners must share one Python runner")
            tables = {owner: rows for owner, rows in validators_by_owner.items() if rows}
            # Definition-only owners share availability, but need no guest table.
            if tables:
                self._handler = SharedHandlerRunner.bind(self._runner, tables)
            self._dispatch = ValidationDispatch(self.definitions, self._handler)
            self._states = dict.fromkeys(self._states, "bound")
        except Exception:
            # Admission can reject malformed input containing separate runners.
            # Close those inputs too; no multi-runner runtime is retained.
            for mod in self._mods.values():
                if type(mod) is SharedSourceMod:
                    mod.close()
            self._unavailable()
            raise
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
                and not self._runner.closed
                and all(state in ("bound", "active") for state in self._states.values()))

    @property
    def status(self):
        return {"ready": self.ready, "owners": dict(self._states)}

    def native_rules(self):
        """Copy admitted switches for installation after career data is acknowledged."""
        self._owning_thread()
        if (self.closed or not self._bound or self._publication is not None or not self._dispatch.ready
                or any(state == "unavailable" for state in self._states.values())
                or (self._runner is not None and self._runner.closed)):
            raise RuntimeError("Native rule policy is unavailable")
        return self._dispatch.native_rules(epoch=self._dispatch.epoch, generation=self._dispatch.generation)

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
        if self.definitions.native_rules:
            # A skipped incompatible switch leaves the original rule in place.
            # Never activate a requested bypass merely because signing installed.
            for name, enabled in self.native_rules().items():
                if components.get(name) != ("native" if enabled else "disabled"):
                    raise ValueError("Incomplete native regulation switch coverage: " + name)
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

    def _unavailable(self):
        # Non-regulation peers use the same interpreter. Its loss fences every
        # required owner, including owners that only contribute definitions.
        self._states = dict.fromkeys(self._states, "unavailable")
        if self._runner is not None:
            self._runner.close()

    def handle(self, message):
        """Only poll() enters this on the Store-owning thread, never socket I/O."""
        self._owning_thread()
        try:
            operation = message["op"]
            if self.closed:
                raise RuntimeError("Regulation runtime is closed")
            # Setup publishes rule data before installing any live game hooks.
            # Data publication precedes coverage acknowledgement. A committed
            # publication may also be retired if loading is cancelled before
            # activation; actions still require acknowledged native ownership.
            if (not self._native and operation not in ("policy-begin", "policy-page", "policy-seal")
                    and not (operation == "reset" and self._publication is None and self._dispatch.ready)):
                raise RuntimeError("Native regulation guards are not acknowledged")
            if operation != "reset" and message["epoch"] != self._dispatch.epoch:
                raise ValueError("Foreign regulation career epoch")
            if operation == "policy-begin":
                if self._publication is not None:
                    raise ValueError("Regulation publication already pending")
                candidate = PolicyPublication(self._dispatch, message)
                affected = candidate.affected
                if self._runner.closed or any(self._states[owner] == "unavailable" for owner in affected):
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
                # committed in the shared runner. Page receipt alone never
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
                expected = {owner: runner.generation for owner, runner in self._dispatch.runners.items()}
                if message["generations"] != expected:
                    raise ValueError("Foreign career reset generations")
                self._dispatch.reset(message["epoch"], deadline=message["deadline"])
                for owner in self._dispatch.runners:
                    self._states[owner] = "bound"
                self._native = self._activation_pending = False
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
            return self._dispatch.evaluate(message["contexts"], message["selections"], calls, deadline=message["deadline"])
        except Exception:
            self._unavailable()
            self._publication = None
            raise

    def poll(self):
        self._owning_thread()
        if self.closed or self._server is None:
            return
        if self._server.failed.is_set() or self._runner.closed:
            self._unavailable()
        # The native channel knows execution owners only. Definition-only owners
        # remain in pack status, but cannot appear as foreign binding identities.
        self._server.poll(self.handle, lambda: sorted(owner for owner in self._dispatch.runners
                                                      if self._states[owner] == "unavailable"))
        if self._server.failed.is_set():
            self._publication = None  # Release partially received policy on terminal I/O failure.
        if self._server.failed.is_set() or self._runner.closed:
            self._unavailable()

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
