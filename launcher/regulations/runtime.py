# SPDX-License-Identifier: MPL-2.0
"""Own handler binding, transport and required-owner readiness for one pack."""

import threading

from launcher.source_mod import SourceMod
from .handler_runner import HandlerRunner
from .server import RegulationServer


class RegulationRuntime:
    """Keep incomplete/unavailable owners required until explicit session closure.

    Binding and a connected channel establish transport readiness only. Step 5
    must acknowledge complete native interception before confirm_native() allows
    publication. Neither JSON nor guest responses can supply that acknowledgement.
    """

    def __init__(self, definitions, mods, wake=None):
        self.definitions, self._mods, self._wake = definitions, mods, wake
        self._thread = threading.get_ident()
        self._states = dict.fromkeys(definitions.owner_ids, "pending")
        self._handlers, self._server = {}, None
        self._native = self.closed = False
        self._attempted, self._bound = False, False

    def _owning_thread(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError("Use regulation runtime on its owning controller thread")

    def bind(self):
        self._owning_thread()
        if self.closed or self._attempted:
            raise RuntimeError("Regulations must bind once in their pack session")
        self._attempted = True
        for owner, validators in self.definitions.validators.items():
            try:
                mod = self._mods[owner]
                if type(mod) is not SourceMod or not mod._active or mod.sandbox.closed or mod.manifest.id != owner:
                    raise ValueError("Regulation source owner is no longer active")
                self._handlers[owner] = HandlerRunner.bind(mod.runner, validators)
                self._states[owner] = "bound"
            except Exception:
                self._states[owner] = "unavailable"
                raise
        if self._states:
            self._server = RegulationServer(self._states, self._wake)
        self._bound = True

    @property
    def endpoint(self):
        if self.closed or self._server is None:
            raise RuntimeError("Regulation transport is not bound")
        return self._server.endpoint

    @property
    def ready(self):
        if self._bound and not self._states:
            return not self.closed  # No declarations require native publication.
        return (not self.closed and self._native and self._server is not None
                and self._server.connected.is_set() and not self._server.failed.is_set()
                and all(state == "bound" and not self._handlers[owner].runner.closed
                        for owner, state in self._states.items()))

    @property
    def status(self):
        return {"ready": self.ready, "owners": dict(self._states)}

    def confirm_native(self):
        """Trusted Step 5 seam, called only after all required native guards bind."""
        self._owning_thread()
        if self.ready and not self._states:
            return
        if (self.closed or self._server is None or not self._server.connected.is_set()
                or self._server.failed.is_set() or any(state != "bound" for state in self._states.values())):
            raise RuntimeError("Incomplete regulation binding cannot become ready")
        self._native = True
        if not self.ready:
            self._native = False
            raise RuntimeError("Regulation handler became unavailable before publication")

    def _unavailable(self, owner):
        self._states[owner] = "unavailable"
        handler = self._handlers.get(owner)
        if handler is not None:
            handler.runner.close()

    def evaluate(self, owner, calls, deadline):
        self._owning_thread()
        if (self.closed or not self._native or self._states.get(owner) != "bound"
                or self._server is None or self._server.failed.is_set() or not self._server.connected.is_set()):
            raise RuntimeError("Required regulation owner is unavailable")
        try:
            return self._handlers[owner].evaluate(calls, deadline)
        except Exception:
            self._unavailable(owner)
            raise

    def poll(self):
        """Process queued calls before UI/control work; never enter a guest on I/O."""
        self._owning_thread()
        if self.closed or self._server is None:
            return
        self._server.poll(self.evaluate)
        for owner, handler in self._handlers.items():
            if self._server.failed.is_set() or handler.runner.closed:
                self._unavailable(owner)

    def close(self):
        self._owning_thread()
        if self.closed:
            return
        self.closed, self._native = True, False
        if self._server is not None:
            self._server.close()
        # SourceMods retain Store ownership and close them in the pack's reverse
        # activation order after transport has stopped; this object never races it.
