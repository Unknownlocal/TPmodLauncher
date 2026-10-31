# SPDX-License-Identifier: MPL-2.0
"""Controller-owned binding, policy publication and pack readiness."""

import threading
from contextlib import contextmanager

from launcher.shared_source_mod import SharedSourceMod
from mod_runtime._regulations.values import integer
from .regulation_handler_bindings import RegulationHandlerBindings
from .voting_runner import VotingRunner
from .publication import PolicyPublication
from .server import RegulationServer
from .validation.dispatch import ValidationDispatch


# These are complete native scopes, not mod-selected function names. A new
# adapter family needs an explicit mapping before its validators can bind.
SCOPE_COMPONENTS = {
    "personnel.approach": frozenset({"personnel_approaches"}),
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
        self._handler, self._runner, self._server, self._dispatch = None, None, None, None
        self._publication, self._action = None, 0
        self.closed = self._attempted = self._bound = self._failed = self.busy = False
        self._native = None  # None -> installed coverage -> confirmed activation.
        self._bridge, self.generation, self._retired = None, None, True
        self._required_components = set()
        self._voting = None

    @property
    def armed(self):
        return self._bridge is not None

    @property
    def failed(self):
        return (self.closed or self._failed
                or self._runner is not None and self._runner.closed
                or self._server is not None and self._server.failed.is_set())

    def arm(self, bridge, pack):
        """Protect career selection after binding, without selecting a save."""
        self._owning_thread()
        categories = self.definitions.custom_categories
        voting = bool(self.definitions.voting)
        data = {"regulations": {"pack": str(pack), "categories": categories}} if categories or voting else {}
        if voting:
            data["regulations"]["voting"] = True
        scripts = self.definitions.custom_scripts
        if scripts:
            data["regulations"]["scripted_changes"] = scripts
        automatic = self.definitions.automatic_changes
        if automatic:
            data["regulations"]["automatic_changes"] = automatic
        status = bridge.request("await-regulations", **data)
        if status.get("adapters", {}).get("career_preload", {}).get("state") != "armed":
            raise RuntimeError("Native regulation loading protection was not confirmed")
        self._bridge = bridge

    @contextmanager
    def _control(self, bridge, generation):
        """Keep UI requests out of native setup and centralise terminal rollback."""
        self._owning_thread()
        if self.failed or self.busy:
            raise RuntimeError("Regulations are not available for native setup")
        control = {"generation": generation, "endpoint": self.endpoint}
        self.busy = True
        try:
            yield control
        except BaseException:
            self._restore(bridge, control)
            raise
        finally:
            self.busy = False

    def _restore(self, bridge, control, *, rules_only=False):
        # Release native socket waits first. Busy native operations still use
        # the bridge's bounded WAIT/retry path before restoring owned wrappers.
        try:
            self.close()
        finally:
            status = bridge.request("restore-native-rules" if rules_only else "stop-regulations", regulations=control)
        if status.get("regulation_control") is not True:
            raise RuntimeError("Native regulation cleanup was not confirmed")

    def _retire(self, bridge, control):
        status = bridge.request("retire-regulations", timeout=3 + 5 * len(self.definitions.owner_ids),
                                regulations=control, _service=self.poll)
        if status.get("regulation_control") is not True:
            raise RuntimeError("Native regulation retirement was not confirmed")
        self._retired = True

    def _prepare(self, bridge, generation):
        with self._control(bridge, generation) as control:
            status = bridge.request("prepare-regulations", timeout=3 + 5 * len(self.definitions.owner_ids),
                                    regulations=control, _service=self.poll)
            if status.get("adapters", {}).get("regulations", {}).get("state") == "prepared":
                return True
            preload = status.get("adapters", {}).get("career_preload", {})
            if preload.get("state") == "left" or preload.get("state") == "waiting" and preload.get("generation") != generation:
                return False  # A healthy cancelled/replaced native job is not a failed pack.
            raise RuntimeError("Native regulation preparation was not confirmed")

    def _activate(self, bridge, generation):
        with self._control(bridge, generation) as control:
            for operation, expected, phase in (("install-regulations", "installed", "installation"),
                                               ("activate-regulations", "active", "activation")):
                data = dict(control, native_rules=self.native_rules()) if expected == "installed" else control
                if expected == "active":
                    # Native activation can replay saved ballots. Keep servicing
                    # that channel while waiting for its bridge acknowledgement.
                    status = bridge.request(operation, regulations=data, _service=self.poll,
                                            timeout=3 + 5 * len(self.definitions.owner_ids))
                else:
                    status = bridge.request(operation, regulations=data)
                if status.get("regulation_control") == "stale":
                    self._retire(bridge, control)
                    return
                native = status.get("adapters", {}).get("regulations", {})
                if (status.get("regulation_control") is not True or native.get("state") != expected
                        or native.get("career_generation") != generation):
                    raise RuntimeError("Native regulation " + phase + " was not confirmed")
                if expected == "installed":
                    self.confirm_native(native.get("components"), pending=True)
                else:
                    self.finish_native_activation()

    def career_preload(self, status, refresh):
        """Own native attempt ordering, retirement and prepare/install/activate."""
        self._owning_thread()
        if not self.armed or self.failed:
            return
        if self.busy:
            raise RuntimeError("Native regulation setup is already in progress")
        if status.get("state") == "unavailable":
            raise RuntimeError("Native regulation loading protection became unavailable")
        if status.get("state") not in ("waiting", "left"):
            return
        generation = status.get("generation")
        if type(generation) is not int or generation < 1:
            raise ValueError("Invalid native regulation loading attempt")
        if self.generation is not None:
            if generation < self.generation or generation == self.generation and status["state"] != "left":
                return
            if not self._retired:
                with self._control(self._bridge, self.generation) as control:
                    self._retire(self._bridge, control)
        # Retain departure IDs too: a delayed waiting notice must not resurrect
        # a cancelled job. A fresh retry has its own strictly newer native ID.
        self.generation = generation
        if status["state"] == "left":
            refresh()
            return
        self._retired = False
        prepared = self._prepare(self._bridge, generation)
        # Refresh cleanup and progress between replies, so UI updates cannot
        # compete with native setup for the same bridge.
        refresh()
        if prepared:
            self._activate(self._bridge, generation)
            refresh()

    def _owning_thread(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError("Use regulation runtime on its owning controller thread")

    def bind(self):
        self._owning_thread()
        if self.closed or self._attempted:
            raise RuntimeError("Regulations must bind once in their pack session")
        self._attempted = True
        try:
            voting = self.definitions.voting
            if voting:
                self._required_components.add("regulation_voting")
            if self.definitions.automatic_changes:
                self._required_components.add("automatic_changes")
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
            if tables or voting:
                self._handler = RegulationHandlerBindings.bind(self._runner, tables, voting=voting)
            if voting:
                self._voting = VotingRunner(self._handler)
            self._dispatch = ValidationDispatch(self.definitions, self._handler)
        except Exception:
            # Admission can reject malformed input containing separate runners.
            # Close those inputs too; no multi-runner runtime is retained.
            for mod in self._mods.values():
                if type(mod) is SharedSourceMod:
                    mod.close()
            self._unavailable()
            raise
        if self._mods:
            catalog = self._dispatch.catalog
            # Preserve the catalog's ascending validator-ID order as wire slots.
            # Native adapters receive subscriptions, never handler Python code.
            metadata = {"epoch": self._dispatch.epoch, "custom": sorted(catalog.custom),
                        "owners": {owner: {"revision": revision, "rules": sorted({row["rule"] for _, row in validators.values()}),
                                           "bindings": [{key: row[key] for key in ("rule", "scope", "inputs") if key in row}
                                                        for _, row in validators.values()]}
                                   for owner, (revision, validators, _) in catalog.owners.items()}}
            revisions = self.definitions.revisions
            for owner, rows in voting.items():
                row = metadata["owners"].setdefault(owner, {"revision": revisions[owner], "rules": [], "bindings": []})
                row["voting"] = [entry["rule"] for entry in rows]
            self._server = RegulationServer(metadata, self._wake)
        self._bound = True

    @property
    def endpoint(self):
        if self.closed or self._server is None:
            raise RuntimeError("Regulation transport is not bound")
        return self._server.endpoint

    @property
    def ready(self):
        if not self._bound or self.failed:
            return False
        if not self._mods:
            return True
        # Bound definition-only owners need no execution policy. For validator
        # owners, dispatch.ready separately requires an active committed view.
        # Neither native guard acknowledgement nor a socket handshake proves it.
        return (self._native == "active" and self._publication is None and self._dispatch.ready
                and self._server.connected.is_set())

    @property
    def status(self):
        """Derive each required mod's status from binding, publication and failure."""
        if self._failed:
            owners = dict.fromkeys(self._mods, "unavailable")
        else:
            # Handler binding precedes listener startup. Definition-only owners
            # stay bound; only validator owners acquire an execution policy.
            owners = dict.fromkeys(self._mods, "pending" if self._dispatch is None else "bound")
            if self._dispatch is not None:
                for owner, state in self._dispatch.states.items():
                    if self._publication is not None:
                        state = "dirty"  # Pages are still being received, before guest installation.
                    elif state == "pending":
                        state = "bound"
                    owners[owner] = state
        return {"ready": self.ready, "failed": self.failed, "owners": owners, "career_generation": self.generation}

    def native_rules(self):
        """Copy admitted switches for installation after career data is acknowledged."""
        self._owning_thread()
        if self.failed or not self._bound or self._publication is not None or not self._dispatch.ready:
            raise RuntimeError("Native rule policy is unavailable")
        return self._dispatch.native_rules(epoch=self._dispatch.epoch, generation=self._dispatch.generation)

    def confirm_native(self, components, *, pending=False):
        """Accept installation coverage for every scope used by this pack.

        Trusted setup supplies the AdapterModules installation report. Signing
        alone cannot cover personnel staffing operations. Other components in
        the report are irrelevant to this pack; policy publication still has
        its own readiness check. Source mods cannot call this acknowledgement.
        With pending=True, coverage is approved but regulation readiness waits
        until the game replies that its installed hooks have been enabled.
        """
        self._owning_thread()
        # A rejected replacement report must not retain a previous approval.
        self._native = None
        if self.failed or not self._bound or (self._mods and not self._server.connected.is_set()):
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
                if components.get(name) != ("regulated" if type(enabled) is dict else "native" if enabled else "disabled"):
                    raise ValueError("Incomplete native regulation switch coverage: " + name)
        if pending and not self._dispatch.ready:
            raise RuntimeError("Native activation needs committed regulation data")
        self._native = "installed" if pending else "active"

    def finish_native_activation(self):
        """Release pack readiness only after the game confirms its hooks enabled."""
        self._owning_thread()
        if self._native != "installed":
            raise RuntimeError("No native regulation activation is pending")
        self._native = "active"
        if not self.ready:
            self._native = None
            raise RuntimeError("Native regulation activation became unavailable")

    def _unavailable(self):
        # Non-regulation peers use the same interpreter. Its loss fences every
        # required owner, including owners that only contribute definitions.
        # Transport can fail while the dispatcher's last policy remains active.
        self._failed = True
        if self._runner is not None:
            self._runner.close()

    def handle(self, message):
        """Only poll() enters this on the Store-owning thread, never socket I/O."""
        self._owning_thread()
        try:
            operation = message["op"]
            if self.failed:
                raise RuntimeError("Regulation runtime is unavailable")
            # Setup publishes rule data before installing any live game hooks.
            # Data publication precedes coverage acknowledgement. A committed
            # publication may also be retired if loading is cancelled before
            # activation; actions still require acknowledged native ownership.
            # Coverage permits validation as soon as native guards enable it.
            # Native guards may call before the activation reply reaches us.
            if (self._native is None and operation not in ("policy-begin", "policy-page", "policy-seal")
                    and not (operation == "reset" and self._publication is None and self._dispatch.ready)):
                raise RuntimeError("Native regulation guards are not acknowledged")
            if operation != "reset" and message["epoch"] != self._dispatch.epoch:
                raise ValueError("Foreign regulation career epoch")
            if operation == "policy-begin":
                if self._publication is not None:
                    raise ValueError("Regulation publication already pending")
                # Block actions during page receipt, before guest installation
                # starts at seal.
                self._publication = PolicyPublication(self._dispatch, message)
                return "staging"
            if operation in ("policy-page", "policy-seal"):
                if self._publication is None:
                    raise ValueError("No regulation publication is pending")
                if operation == "policy-page":
                    self._publication.append(message)
                    return "staging"
                # Seal returns only after the complete policy is installed and
                # committed in the shared runner. Page receipt alone never
                # changes owner readiness to active.
                self._publication.seal(self._dispatch, message)
                self._publication = None
                return "active"
            if self._publication is not None:
                raise ValueError("Regulation publication has fenced actions")
            if operation == "reset":
                # Reset retires the entire career, not just owners named by an
                # action. Its generation map must describe all execution views.
                # After reset, installed generations are zero even though the
                # session publication counter keeps increasing.
                generation = self._dispatch.generation if self._dispatch.ready else 0
                expected = dict.fromkeys(self._dispatch.states, generation)
                if message["generations"] != expected:
                    raise ValueError("Foreign career reset generations")
                self._dispatch.reset(message["epoch"], deadline=message["deadline"])
                self._native = None
                return "reset"
            if operation == "vote":
                owner = message["owner"]
                if (self._voting is None or not self._dispatch.ready
                        or self._dispatch.states.get(owner) != "active"
                        or message["generations"] != {owner: self._dispatch.generation}):
                    raise ValueError("Unavailable or foreign voting owner")
                rule = message["regulation"]["key"]
                _, models, _ = self.definitions._catalog_inputs()
                # Option overlays are admitted per owner, never taken from the game.
                overlays = models[owner].option_data.get(rule, {})
                regulation = dict(message["regulation"])
                for name in ("current", "proposed"):
                    option = regulation[name]
                    regulation[name] = dict(option, option_data=overlays.get(option["key"], {}))
                return self._voting.evaluate(owner, message["snapshot"], regulation, message["contexts"], deadline=message["deadline"])
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
                if (self._dispatch.states[owner] != "active" or type(row) is not dict or set(row) != {"revision", "calls"}
                        or row["revision"] != view.revision or message["generations"][owner] != self._dispatch.generation
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
        previous, self.busy = self.busy, True
        try:
            if self._server.failed.is_set() or self._runner.closed:
                self._unavailable()
            # The native channel knows execution owners only. Definition-only owners
            # remain in pack status, but cannot appear as foreign binding identities.
            self._server.poll(self.handle, lambda: sorted(self._dispatch.states) if self._failed else [])
            if self._server.failed.is_set():
                self._publication = None  # Release partially received policy on terminal I/O failure.
            if self._server.failed.is_set() or self._runner.closed:
                self._unavailable()

            # A setup request services this poll too. Restore native helpers only
            # after that outer request replies, avoiding a second busy bridge call.
            if (not previous and self._bridge is not None and self.generation is not None and self.failed
                    and any(enabled is not True for values in self.definitions.native_rules.values() for enabled in values.values())):
                self._restore(self._bridge, {"generation": self.generation, "endpoint": self.endpoint}, rules_only=True)
        finally:
            self.busy = previous

    def close(self):
        self._owning_thread()
        if self.closed:
            return
        self.closed, self._native = True, None
        self._publication = None
        if self._server is not None:
            self._server.close()
        # PackMods closes its shared/independent Stores after socket I/O stops;
        # transport never races guest teardown.
