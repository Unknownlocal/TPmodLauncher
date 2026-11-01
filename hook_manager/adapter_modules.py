# SPDX-License-Identifier: MPL-2.0
"""Load bundled game adapters as private modules without changing the game path."""

import importlib
import importlib.util
import sys
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType

if __package__:
    from .diagnostics import build_log_record, single_line
    from .manager import HookError
    from .career_preload import CareerPreload

NATIVE_RULE_GROUPS = {"rookie_contract_length_check": "drivers.rookie-check",
                      "staff_prestige_check": "staff.prestige-check"}


class AdapterModules:
    """Own one bridge's fixed module bundle and its temporary import finder.

    The launcher supplies these sources, not packs or control requests. Separate
    module dictionaries keep identically named helpers in different adapters
    independent. The existing manager/callable classes are shared by reference.
    Loading definitions installs no game hooks and grants no policy readiness.
    """

    def __init__(self, host, sources):
        self.host, self.sources = host, sources
        self.prefix = host.__name__ + ".adapter_code"
        self.modules = {}
        self.state = "idle"
        self.error = None
        self.regulations = []
        self.channel = None
        self.coverage = {}
        self.regulation_error = None
        self.policy = None
        self.regulation_generation = None
        self.preload = None
        self.categories = None
        self.regulation_changes = None
        self.regulation_screen = None
        # UI imports and game-worker validation can fail after setup returned.
        # Keep a bounded pack-owned outbox for the next ordinary bridge response.
        self.regulation_notices = deque(maxlen=16)
        self.enqueue_log_record = self.regulation_notices.append

    def status(self):
        """Distinguish prepared data, installed hooks and unfinished cleanup.

        Successful publication alone supplies no hook coverage or readiness.
        Failed installation can still own hooks; failed preparation owns none.
        """
        result = {"state": self.state, "modules": len(self.sources) if self.state == "loaded" else 0}
        if self.regulation_screen is not None:
            self.regulation_screen.poll()
        if self.error is not None:
            result["detail"] = self.error
        if self.preload is not None:
            result["career_preload"] = self.preload.loading_status()
            if self.categories is not None and self.categories.errors:
                result["career_preload"] = {"state": "unavailable", **self.categories.error_record}
        if self.channel is not None or self.regulation_error:
            # Installing all required native hooks does not yet activate checks.
            state = ("cleanup_pending" if self.channel is not None else "unavailable") if self.regulation_error else (
                "installed" if self.coverage else "prepared" if self.policy is not None else "bound")
            result["regulations"] = {"state": state,
                                     "components": dict(self.coverage)}
            if self.regulation_generation is not None and self.coverage and not self.regulation_error:
                result["regulations"]["state"] = "active" if self.channel.validation_ready else "installed"
            if self.regulation_generation is not None:
                result["regulations"]["career_generation"] = self.regulation_generation
            if self.regulation_error:
                result["regulations"]["detail"] = self.regulation_error
        return result

    def await_regulations(self, definitions=None):
        """Extend native preload completion before trusted setup releases Load Game.

        Optional category data comes from enabled, bound owners. The fixed
        loader runs before native defaults and saved selections are restored.
        Mods cannot select either wrapper; arming alone does not approve checks.
        """
        self.host.manager._main()
        if self.state == "closed" or self.channel is not None or self.preload is not None or self.categories is not None:
            raise HookError("Career preloading needs an unused adapter installation")
        try:
            if definitions is not None:
                if type(definitions) is not dict:
                    raise HookError("Expected prepared regulation declarations")
                definitions = dict(definitions)
                voting = definitions.pop("voting", False)
                if type(voting) is not bool:
                    raise HookError("Invalid voting preparation flag")
                self.load()
                if self.state != "loaded":
                    raise HookError(self.error or "Adapter modules are not loaded")
                loader = self.host.adapter_code.game_adapters.regulations.regulation_category
                self.start_regulation_changes()
                if self.regulation_changes is None:
                    raise HookError("Native regulation change load/save integration is unavailable")
                if voting:
                    self.regulation_changes.defer_voting(definitions.get("pack"))
                if definitions.get("categories"):
                    self.categories = loader.RegulationCategoryAdapter(definitions, self.regulation_changes)
                    screen = self.host.adapter_code.game_adapters.regulations.regulation_screen
                    self.regulation_screen = screen.RegulationScreenAdapter(self.enqueue_log_record, self.categories)
                elif not voting or set(definitions) != {"pack", "categories"} or definitions["categories"] != []:
                    raise HookError("Expected prepared categories or voting protection")
            self.preload = CareerPreload(self._career_left, getattr(self.host, "readiness", None))
        except BaseException:
            self.stop_regulations()
            raise
        return self.status()

    def start_regulation_changes(self):
        """Keep private save metadata through bridge lifetime, even without mods."""
        try:
            self.load()
            if self.state != "loaded":
                raise HookError(self.error or "Adapter modules are not loaded")
            if self.regulation_changes is None:
                module = self.host.adapter_code.game_adapters.regulations.regulation_change
                self.regulation_changes = module.RegulationChangeAdapter(
                    self.enqueue_log_record, self.host.manager.completed)
        except Exception as error:
            self.enqueue_log_record(build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED regulation changes: "
                + str(error) + "; load/save integration skipped; continuing native game", error=error, component="regulations"))
        return True  # Finish the one-shot native import observation, including failure.

    def regulation_defaults(self):
        """Read fixed native defaults for the process's captured startup pack."""
        self.host.manager._main()
        pack = self.host.discovery.poll()
        if pack.get("state") != "ready" or pack.get("series") != "formula":
            raise HookError("Regulation defaults need the selected Formula pack")
        self.load()
        if self.state != "loaded":
            raise HookError(self.error or "Adapter modules are not loaded")
        reader = self.host.adapter_code.game_adapters.regulations.regulation_native_defaults
        categories = reader.read_regulation_defaults(pack["root"])
        return {"pack": pack["root"], "categories": categories} if categories is not None else None

    def _career_left(self):
        """Stop new mod-checked actions; retirement waits for admitted work."""
        if self.channel is not None:
            self.channel.validation_ready = False

    def prepare_regulations(self, request, *, submit_log):
        """Publish a career snapshot through a fixed endpoint, without game hooks.

        Only trusted bridge setup calls this. Pack data cannot supply a module,
        callback or native target. The controller must service publication while
        waiting for this main-thread operation; a socket connection is not enough.
        """
        self.host.manager._main()
        if (type(request) is not dict or set(request) != {"generation", "endpoint"}
                or type(request["generation"]) is not int or request["generation"] < 1):
            raise HookError("Invalid regulation preparation fields")
        if self.preload is not None and request["generation"] != self.preload.generation:
            # Reject before acquiring resources: cleanup for an old request
            # must not dismantle the newly captured loading attempt.
            submit_log(build_log_record('DEBUG', "REGULATIONS COMPATIBILITY FAILED preparation: stale loading attempt; request skipped", component="regulations"))
            return self.status()
        if self.channel is not None and self.policy is not None:
            raise HookError("Regulation preparation already owns a channel")
        # Cancellation or a newer queued attempt is normal, not a broken mod.
        # Check before acquiring resources or invoking failure cleanup.
        if self.preload is not None:
            try:
                self.preload.current(request["generation"])
            except HookError:
                return self.status()
        self.regulation_error = None
        channel = None
        try:
            self.load()
            if self.state != "loaded":
                raise HookError(self.error or "Adapter modules are not loaded")
            if self.preload is None:
                raise HookError("Regulation preparation needs a captured native loading attempt")
            generation = request["generation"]
            state = self.preload.current(generation)
            if self.categories is not None:
                self.categories.check(state)
            channel = self.channel
            if channel is None:
                channel = self.host.adapter_code.game_adapters.regulations.channel.RegulationChannel(request["endpoint"])
            elif request["endpoint"].get("token") != channel._token:
                raise HookError("Regulation preparation does not own this connection")
            channel.validation_ready = False
            native = self.host.adapter_code.game_adapters.regulations.regulation_policy
            policy = native.RegulationPolicy(state, channel)
            policy.publish()
            # Publication releases the GIL while waiting. A delayed reply must
            # not associate the old snapshot with a newly loaded career.
            if self.preload.current(generation) is not state:
                raise HookError("The prepared regulation career is no longer current")
            self.channel, self.policy = channel, policy
            self.regulation_generation = generation
        except Exception as error:
            self.stop_regulations()  # Failed preparation must not hold native loading completion.
            self.regulation_error = single_line(error, 512) or type(error).__name__
            submit_log(build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED preparation: " + self.regulation_error
                   + "; regulation setup skipped; continuing native game", error=error, component="regulations"))
        finally:
            # Keep successful preparation until explicit activation or cleanup.
            # Any failed candidate closes before reporting its unavailable state.
            if channel is not None and self.channel is not channel:
                channel.close()
        return self.status()

    def control_regulations(self, operation, request, *, submit_log):
        """Install, enable or stop only this request's prepared career snapshot.

        The endpoint token ties delayed control requests to their own connection.
        Coverage approval happens in the launcher between installation and enable.
        Stopping returns False while an admitted operation still needs its hooks.
        A stale loading attempt returns "stale" so setup retires only its career.
        """
        self.host.manager._main()
        fields = {"generation", "endpoint"}
        if operation == "install-regulations" and type(request) is dict and "native_rules" in request:
            fields.add("native_rules")
        if (operation not in ("install-regulations", "activate-regulations", "stop-regulations", "retire-regulations", "restore-native-rules")
                or type(request) is not dict or set(request) != fields
                or type(request["generation"]) is not int or request["generation"] < 1
                or type(request["endpoint"]) is not dict or set(request["endpoint"]) != {"port", "token"}):
            raise HookError("Invalid regulation control fields")
        if operation == "retire-regulations":
            if self.channel is None:
                return True  # Cancellation before publication owns no career data.
            if request["endpoint"]["token"] != self.channel._token:
                raise HookError("Regulation retirement does not own this connection")
            if self.policy is None:
                return True  # The same retirement was already acknowledged.
        if operation == "stop-regulations" and self.channel is None:
            if self.preload is not None and request["generation"] != self.preload.generation:
                # An old cleanup reply must not release a rearmed loading job.
                raise HookError("Regulation control does not own this loading attempt")
            return self.stop_regulations()  # Also restore preload aliases before channel acquisition.
        if (self.channel is None or request["generation"] != self.regulation_generation
                or type(request["endpoint"]["token"]) is not str
                or request["endpoint"]["token"] != self.channel._token):
            raise HookError("Regulation control does not own this prepared connection")
        if operation == "stop-regulations":
            self.channel.validation_ready = False
            return self.stop_regulations()
        if operation == "restore-native-rules":
            # Failed validators must keep refusing actions. Restore only the
            # bypass helpers; leave signing/project guards and their ownership.
            self.channel.validation_ready = False
            for hook in tuple(self.regulations):
                if hook.groups.keys() & set(NATIVE_RULE_GROUPS.values()):
                    hook.stop()
                    self.regulations.remove(hook)
            for key in NATIVE_RULE_GROUPS:
                self.coverage[key] = "native"
            return True
        if operation == "retire-regulations":
            channel = self.channel
            channel.validation_ready = False
            if not self.stop_regulations(retain=True):
                return False
            channel.reset(channel.epoch + 1)
            return True
        # Native input can cancel between the separate preparation/installation
        # replies. Preserve the connection so the host can retire this policy.
        try:
            state = self.preload.current(self.regulation_generation)
        except HookError:
            self.channel.validation_ready = False
            return "stale"
        try:
            policy, channel = self.policy, self.channel
            if state is not policy.state:
                raise HookError("The prepared regulation career is no longer current")
            if self.categories is not None:
                self.categories.check(state)
            policy._current()
            if not policy._published or any(policy._adoption(rule) != row for rule, row in policy._adopted.items()):
                raise HookError("Native regulation choices need a new preparation")
            if operation == "install-regulations":
                if self.coverage:
                    raise HookError("Prepared regulation hooks are already installed")
                self.install_regulations(policy.state, channel, policy.select,
                                         native_rules=request.get("native_rules", {}), submit_log=submit_log)
            else:
                if not self.coverage or channel.validation_ready:
                    raise HookError("Regulation activation needs a pending installation")
                for hook in self.regulations:
                    if hook.closed or any(row["state"] != "active" for row in hook.groups.values()):
                        raise HookError("Regulation hooks are no longer installed")
                    if any(not hook.group_intact(name) for name in hook.groups):
                        raise HookError("Another owner replaced a regulation hook")
                self._check_voting_preload(policy.state, channel)
                self.preload.allow(policy.state)
                channel.validation_ready = True
                for hook in self.regulations:
                    resume = getattr(hook, "resume", None)
                    if resume is not None:
                        resume()
            return True
        except Exception as error:
            # Block new wrapped attempts before trying to restore the group.
            # Busy cleanup retains ownership until the stop request can retry.
            if self.channel is not None:
                self.channel.validation_ready = False
            self.regulation_error = single_line(error, 512) or type(error).__name__
            self.stop_regulations()
            raise

    def install_regulations(self, state, channel, select, *, native_rules, submit_log):
        """Own the fixed adapters for a career supplied by trusted native setup.

        This Python-only method is absent from the bridge request API. The
        caller supplies the identified career, authenticated channel and native
        rule selector; no mod paths or author Python enter this loader. Once the
        initial career/channel checks pass, this owner also cleans up failed setup.
        Preparation and control check the captured loading attempt; this method
        only constructs its hooks from those already prepared inputs.
        """
        self.host.manager._main()
        if self.state != "loaded" or (self.channel is not None and (
                self.channel is not channel or self.coverage or self.regulations)):
            raise HookError("Regulation installation needs an unused loaded adapter bundle")
        game = sys.modules.get("game_state")
        career = getattr(game, "GameState", None)
        if not isinstance(career, type) or not isinstance(state, career):
            raise HookError("Regulation installation needs a native GameState")
        if channel is None or channel.closed or not callable(select) or not callable(submit_log):
            raise HookError("Regulation installation needs an open channel and trusted callbacks")
        adapters = self.host.adapter_code.game_adapters.regulations
        self.channel = channel
        try:
            approach = adapters.regulation_approach.RegulationApproachAdapter.prepare(
                state, channel, select, self.enqueue_log_record)
            personnel = []
            # Unsubscribed packs do not trigger personnel inspection or hooks.
            if channel.bindings("personnel.contract"):
                try:
                    # Constructors only prepare support. A late academy failure
                    # must not leave earlier driver/staff/staffing wrappers behind.
                    personnel.append(adapters.regulation_personnel.RegulationPersonnelAdapter(
                        state, channel, select, submit_log=self.enqueue_log_record))
                    personnel.append(adapters.regulation_academy.RegulationAcademyAdapter(
                        state, channel, select, submit_log=self.enqueue_log_record))
                except Exception as error:
                    for hook in reversed(personnel):
                        hook.stop()
                    personnel.clear()
                    # This scope requires all four components; partial coverage
                    # cannot activate a pack, so skip the group together.
                    submit_log(build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED scope=personnel.contract: "
                           + str(error)
                           + "; personnel integration skipped; continuing native game", error=error, component="regulations"))
            # All required personnel support is checked before the first native
            # assignment, including these independently requested rule switches.
            self.regulations.extend(personnel)
            if approach is not None:
                self.regulations.append(approach)
            self.regulations.extend(adapters.regulation_native_switches.install_regulation_switches(state, native_rules, submit_log=submit_log))
            for hook in personnel:
                hook.install_prepared()
            if approach is not None:
                approach.personnel = personnel[0] if personnel else None
                approach.install_prepared()
            self.coverage["personnel_approaches"] = approach is not None
            for key in ("driver_signing", "staff_signing", "staffing", "academy_signing"):
                self.coverage[key] = bool(personnel)
            project = adapters.regulation_project.RegulationProjectAdapter.create(state, channel, select, submit_log)
            if project is not None:
                self.regulations.append(project)
            self.coverage["development_projects"] = project is not None
            self._check_voting_preload(state, channel)
            voting = adapters.regulation_voting.RegulationVotingAdapter.create(state, channel, self.enqueue_log_record)
            if voting is not None:
                self.regulations.append(voting)
                self.coverage["regulation_voting"] = True
            groups = {name for hook in self.regulations for name, row in hook.groups.items() if row["state"] == "active"}
            # A requested bypass is "disabled" only if its wrapper installed.
            # "skipped" means compatibility checks left that native rule intact.
            for key, group in NATIVE_RULE_GROUPS.items():
                value = native_rules.get(key, True)
                if value is True:
                    self.coverage[key] = "native"
                elif group not in groups:
                    self.coverage[key] = "skipped"
                else:
                    self.coverage[key] = "regulated" if type(value) is dict else "disabled"
            if self.categories is not None and self.categories.automatic is not None:
                helper = self.categories.automatic
                helper.check(state)
                helper.state, helper.channel = state, channel
                self.coverage["automatic_changes"] = True
            return dict(self.coverage)
        except BaseException as error:
            # A guard may already have admitted a worker. Retain ownership and
            # identify failed setup until an idle cleanup retry can restore it.
            self.regulation_error = single_line(error, 512) or type(error).__name__
            self.stop_regulations()
            raise

    def _check_voting_preload(self, state, channel):
        """Do not approve voting after unprotected native save loading."""
        if getattr(channel, "voting", {}):
            if self.regulation_changes is None or self.regulation_changes.voting_pack != Path(state.data_dir).resolve():
                raise HookError("Voting needs this pack's native loading protection")
            self.regulation_changes.check()

    def stop_regulations(self, *, retain=False):
        """Restore owned hooks only when no admitted operation still needs them.

        Acquire the guard registries together before checking their active
        operations. Otherwise a worker could enter an earlier guard while a
        later one is being stopped, leaving a half-removed installation.
        Retirement retains the connection and loading observer: the next career
        reuses admitted handlers, but must publish data and approve coverage anew.
        """
        self.host.manager._main()
        # Keep a partially constructed loader reachable until its active calls
        # finish; its constructor may not have reached the normal assignment.
        if self.categories is None and self.regulation_changes is not None:
            self.categories = self.regulation_changes.categories
        with ExitStack() as locks:
            # Category loading belongs to the pack, so career retirement keeps
            # it installed. Full cleanup must also wait for its loading workers.
            hooks = [*self.regulations, *([self.categories] if self.categories is not None and not retain else []),
                     *([self.regulation_screen] if self.regulation_screen is not None and not retain else [])]
            # Retirement retains pack hooks, but must still wait for weekly
            # decisions and persistence before changing careers.
            for hook in [*hooks, *([self.categories] if retain and self.categories is not None
                                   and self.categories.automatic is not None else []),
                         *([self.regulation_changes] if self.regulation_changes is not None else [])]:
                lock = getattr(hook, "_lock", None)
                if lock is not None:
                    if not lock.acquire(blocking=False):
                        return False
                    locks.callback(lock.release)
                    if hook._active:
                        return False
            for hook in reversed(hooks):
                hook.stop()
            if not retain:
                self.categories = None
                self.regulation_screen = None
                if self.regulation_changes is not None:
                    self.regulation_changes.defer_voting(None)
            if self.channel is not None and not retain:
                self.channel.close()
            self.regulations.clear()
            if not retain:
                self.channel = None
            self.coverage.clear()
            self.regulation_error = None
            self.policy = None
            self.regulation_generation = None
            if self.preload is not None and not retain:
                self.preload.stop()
                self.preload = None
        return True

    def load(self):
        """Import only bundled definitions, rolling back a failed load completely."""
        self.host.manager._main()
        if self.state == "closed":
            raise HookError("Adapter modules are closed")
        if self.state != "idle":
            return self.status()
        try:
            if (hasattr(self.host, "adapter_code") or any(
                    name == self.prefix or name.startswith(self.prefix + ".") for name in tuple(sys.modules))):
                raise HookError("Another module owns the private adapter namespace")
            root = ModuleType(self.prefix)
            root.__package__, root.__path__ = self.prefix, []
            root.__spec__ = importlib.util.spec_from_loader(self.prefix, loader=None, is_package=True)
            self.modules[self.prefix] = sys.modules[self.prefix] = root
            self.host.adapter_code = root
            # Adapter relative imports reuse the running infrastructure. Do not
            # create a second Manager class or a second callable registry.
            for name in ("manager", "callables", "diagnostics"):
                alias = self.prefix + "." + name
                self.modules[alias] = sys.modules[alias] = self.host
                setattr(root, name, self.host)
            sys.meta_path.insert(0, self)
            for name in self.sources:
                importlib.import_module(self.prefix + "." + name)
            self.state = "loaded"
        except Exception as error:
            self._remove()
            self.state = "failed"
            self.error = single_line(error, 512) or type(error).__name__
        except BaseException:
            self._remove()
            raise
        finally:
            # Every bundled module is now loaded. Keep no persistent importer
            # that could intercept future game or third-party imports.
            if self in sys.meta_path:
                sys.meta_path.remove(self)
        return self.status()

    def find_spec(self, fullname, path=None, target=None):
        """Resolve our private names from the fixed bundle, never the filesystem."""
        if not fullname.startswith(self.prefix + "."):
            return None
        name = fullname[len(self.prefix) + 1:]
        if name not in self.sources:
            raise ModuleNotFoundError("Module is not in the trusted adapter bundle: " + name)
        return importlib.util.spec_from_loader(fullname, self, is_package=self.sources[name][2])

    def create_module(self, spec):
        return None  # Let Python initialise package/import metadata normally.

    def exec_module(self, module):
        name = module.__name__[len(self.prefix) + 1:]
        filename, source, package = self.sources[name]
        self.modules[module.__name__] = module
        exec(compile(source, filename, "exec"), module.__dict__)

    def _remove(self):
        for name, module in reversed(tuple(self.modules.items())):
            # Leave a replacement owned by somebody else intact.
            if sys.modules.get(name) is module:
                del sys.modules[name]
        root = self.modules.get(self.prefix)
        if root is not None and getattr(self.host, "adapter_code", None) is root:
            del self.host.adapter_code
        self.modules.clear()

    def close(self):
        """Stop owned adapters before removing definitions; defer busy workers."""
        if self.state == "closed":
            return True
        self.host.manager._main()
        with ExitStack() as locks:
            if self.regulation_changes is not None:
                if not self.regulation_changes._lock.acquire(blocking=False):
                    return False
                locks.callback(self.regulation_changes._lock.release)
                if self.regulation_changes._active:
                    return False
            if not self.stop_regulations():
                return False
            if self.regulation_changes is not None:
                self.regulation_changes.stop()
                self.regulation_changes = None
            self._remove()
            self.state = "closed"
        return True
