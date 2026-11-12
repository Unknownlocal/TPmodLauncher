# SPDX-License-Identifier: MPL-2.0
"""Discover and own sandboxed code mods for one process's selected Formula pack."""

from launcher.diagnostics import activity_scope, bind_reporter
from hook_manager.diagnostics import error_text

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from time import perf_counter
import tomllib
import weakref

from launcher.bundled_dependencies import local_path, safe_name
from launcher.load_timing import measure
from launcher.compiled_mod import CompiledMod
from launcher.mod_contract import Manifest, ManifestError, Mod, _host_operations
from launcher.pack_candidate import PackCandidate
from launcher.prerequisites import Prerequisites
from launcher.shared_source_mod import SharedSourceMod
from launcher.ui_settings import SettingsRequests
from launcher.ui_publication import UiPublication
from hook_manager.menu import MenuRequests
from hook_manager.themes import ThemeRequests
from mod_runtime.bundle import RunnerBundle
from mod_runtime.preparation import SourcePreparation
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest, read_manifest

# Bound discovery and the number of mods admitted by one game process.
MAX_PACK_ENTRIES = 4096
MAX_MODS = 32


def _ui_areas(capabilities):
    """Keep an activation's immediate UI failures with its own permissions."""
    return tuple(area for area, capability in (("menu", "main-menu"), ("settings", "ui-settings"), ("themes", "ui-settings"))
                 if capability in capabilities)


def _read_component(manifest, timings):
    """Read bounded portable bytes; compilation is coordinated by the controller."""
    with measure(timings, "component_read"):
        return manifest.read_component()


class PackMods:
    """Load once on the controller thread; close all instances with their game session."""

    def __init__(
        self,
        game_root,
        submit_log,
        *,
        set_ui=None,
        set_loading=None,
        background=False,
        cache=None,
        runner_bundle=None,
        wake=None,
        bridge=None,
    ):
        # game_root and submit_log come from the launcher, never from a mod manifest.
        self.game_root = Path(game_root).resolve(strict=True)
        self.submit_log = bind_reporter(submit_log, component="mods")
        self.mods = {}
        self.started = False
        self.closed = False
        self.menu = MenuRequests()
        self.settings = SettingsRequests()
        self.ui = UiPublication(set_ui)
        self._ui_rejecting = False
        self.last_settings_request = 0
        self.settings_result = None
        self.themes = ThemeRequests()
        self.set_loading = set_loading
        self.background = background
        self.cache = cache
        if runner_bundle is not None and type(runner_bundle) is not RunnerBundle:
            raise ManifestError("Expected a verified runner bundle")
        self.runner_bundle = runner_bundle
        self.executor = None
        self.pending = None
        self.loading = None
        self.total = self.failed = 0
        self.finished = False
        # Keep fixed membership after failure: unactivated peers still belong
        # in rejection counts and logs. Entries themselves need no second owner
        # once they have moved into the live mods map.
        self._sources, self._source_entries = {}, {}
        self._source_runner = None
        # Starting withholds import-time registrations; unavailable stops later
        # activations; reported prevents counting/logging the same failure twice.
        self._source_starting = self._source_unavailable = self._source_reported = False
        self._pack_root = None
        self._regulations_prepared = False
        self._regulation_error = False
        self.regulations, self._regulation_status = None, None
        self._regulation_owners, self._regulation_wake = set(), wake
        owner = weakref.ref(self)
        def ready(_=None):
            scope = owner()
            if scope is not None and not scope.closed and scope._regulation_wake is not None:
                scope._regulation_wake()
        self._ready = ready
        self._bridge = bridge
        self.prerequisites = Prerequisites()

    @property
    def _regulation_failed(self):
        return (self._regulation_error or bool(self._regulation_owners and self._source_unavailable)
                or self.regulations is not None and self.regulations.failed)

    def set_wake(self, wake):
        """Connect completion before load; callbacks only queue owner-thread work."""
        self._regulation_wake = wake

    @property
    def _regulation_dispatching(self):
        return self.regulations is not None and self.regulations.busy

    @property
    def _regulation_armed(self):
        return self.regulations is not None and self.regulations.armed

    def _start_regulations(self):
        """Bind once, then protect native loading before enabling career selection."""
        if (self._bridge is None or not self.finished or not self._regulation_owners
                or self._regulations_prepared or self._regulation_failed):
            return
        self._check_process()
        from launcher.regulations.runtime import SCOPE_COMPONENTS

        self.bind_regulations(scopes=set(SCOPE_COMPONENTS),
                              native_categories=lambda: self._bridge.regulation_defaults(self._pack_root))
        if self._regulation_owners:
            self.regulations.arm(self._bridge, self._pack_root)
        self._progress("complete")

    def career_preload(self, status):
        """Refresh runtime status between completed native setup requests."""
        if not self.closed and self.regulations is not None:
            self.regulations.career_preload(status, self.poll)

    def _regulation_waiting(self):
        return (not self.closed and (self._regulation_failed or (bool(self._regulation_owners)
                and (self.regulations is None or not self.regulations.ready))))

    def _ui_waiting(self):
        # Imports can register UI for members not yet activated. Publish only
        # after pack startup; cleanup removes a failed owner's declarations.
        # Career selection and retirement do not change pack-owned UI.
        return not self.closed and self._source_starting

    def _visible_ui(self, area, records):
        """Keep waiting registrations private without delaying their owner's code."""
        if self._ui_waiting():
            return []
        if not self.prerequisites.declarations:
            return records
        return [record for record in records if self.prerequisites.allows(
            record["owner"], "menu:" + record["id"] if area == "menu" else area)]

    def readiness_status(self, status):
        """Refresh dependent UI on native changes, outside a busy setup request."""
        previous = self.prerequisites.native
        # Activity belongs to diagnostics. Keep all other fields so the strict
        # prerequisite validator still rejects malformed availability records.
        availability = (
            {key: value for key, value in status.items() if key != "activity"}
            if type(status) is dict else status
        )
        self.prerequisites.receive(availability)
        if status.get("detail") and status.get("detail") != previous.get("detail"):
            self._log("PREREQUISITES UNAVAILABLE", level="WARNING", detail=status["detail"])
        if previous != self.prerequisites.native and not self._regulation_dispatching:
            self._publish_ui()

    def bind_regulations(self, *, scopes, native_categories=()):
        """Bind admitted handlers; native interception needs a separate acknowledgement."""
        from launcher.regulations.runtime import RegulationRuntime

        try:
            for owner in self._regulation_owners:
                mod = self.mods.get(owner)
                if type(mod) is not SharedSourceMod or not mod._active or mod.sandbox.closed:
                    raise ManifestError("Required regulation owner became unavailable before binding")
            definitions = self.prepare_regulations(scopes=scopes, native_categories=native_categories)
            if definitions is None:
                self._regulation_owners.clear()
                self._publish_ui()
                self._progress("complete")
                return None
            # Capability requests with no selected declaration contribute no resources.
            # Keep declared/unavailable owners required; cleanup is not disabling.
            self._regulation_owners = set(definitions.owner_ids)
            self.regulations = RegulationRuntime(definitions, self.mods, self._ready)
            self.regulations.bind()
        except Exception as error:
            # Preparation can fail before there is a runtime, and listener startup
            # can fail after handlers bind. Neither can revert to loading/ready.
            self._regulation_error = True
            self._log("REGULATIONS FAILED", level="ERROR", owners=sorted(self._regulation_owners), detail=str(error)[:512], error=error)
            try:
                self._reject_mods(self._regulation_owners, str(error)[:512])
            except Exception as cleanup_error:
                self._log("CLEANUP ERROR", level="ERROR", detail=str(cleanup_error)[:512], error=cleanup_error)
            self._progress("error", error=error)
            raise
        if self.regulations.ready:
            self._publish_ui()
            self._progress("complete")
        return self.regulations

    def prepare_regulations(self, *, scopes, native_categories=()):
        """Prepare once from admitted active source owners, without publishing rules.

        Source API 0.4.0 owners request the regulations permission. Game validation
        needs bound rules, published data and confirmed native coverage.
        """
        from launcher.regulations import RegulationDefinitions

        if self.closed or not self.finished or self._pack_root is None or self._regulations_prepared:
            raise ValueError("Regulations require a completed pack and one preparation per session")
        sources = {owner: mod.snapshot for owner, mod in self.mods.items()
                   if type(mod) is SharedSourceMod and mod._active and not mod.sandbox.closed
                   and "regulations" in mod.manifest.capabilities}
        # Freeze preparation even on failure: editing a live pack cannot silently
        # repair/redefine an enabled rule; recovery needs a fresh admission.
        self._regulations_prepared = True
        try:
            return RegulationDefinitions.read(self._pack_root, sources, scopes=scopes,
                                              native_categories=native_categories)
        finally:
            # Admission is one-shot even on failure. The guest keeps its source
            # for lazy imports; these host copies have no further consumer.
            for mod in self.mods.values():
                if type(mod) is SharedSourceMod:
                    mod.snapshot = None

    def _flush_ui(self, areas=("menu", "settings", "themes")):
        """Publish one changed snapshot after guest execution has returned."""
        if self.prerequisites.declarations:
            self.prerequisites.update(
                (owner for owner, mod in self.mods.items() if mod._active and not mod.sandbox.closed),
                self.regulations is not None and self.regulations.ready)
        registries = {"menu": self.menu.buttons, "settings": self.settings.records, "themes": self.themes.records}
        values = {area: self._visible_ui("theme" if area == "themes" else area, registries[area]) for area in areas}
        if self._ui_waiting():
            self.settings_result = None
        try:
            self.ui.publish(values, settings_result=self.settings_result)
        finally:
            if "settings" in values and self.ui.applied["settings"] is not None:
                self.settings_result = None

    def _flush_settings(self):
        # Save acknowledgements are explicit commands, independent of other UI.
        self._flush_ui(("settings",))

    def _remove_mod(self, owner):
        """Revoke an independent component's owned registrations."""
        self._remove_mods((owner,))

    def _remove_mods(self, owners):
        """Remove all affected declarations before sending any replacement registry."""
        owners = set(owners)
        for owner in owners:
            self.mods.pop(owner, None)
            self.menu.remove(owner)
            self.settings.remove(owner)
            self.themes.remove(owner)
        # Native validation holds the game thread until its reply. Defer UI
        # replacement until that request has returned to the controller.
        if not self.closed and not self._regulation_dispatching and not self._ui_rejecting:
            self._flush_ui()

    def _reject_sources(self, detail):
        """Count and identify the entire failed group once, including unactivated peers."""
        self._source_unavailable = True
        self._source_starting = False
        if self.closed or self._source_reported:
            return
        self._source_reported = True
        self.failed += len(self._sources)
        error, detail = detail, str(detail)[:512]
        self._log("SOURCE GROUP FAILED", level="ERROR", owners=list(self._sources), detail=detail,
                  error=error)
        # The group error identifies its owners; individual details use DEBUG.
        for owner, candidate in self._sources.items():
            self._log("REJECTED", level="DEBUG", id=owner, folder=candidate.root.name, detail=detail)

    def _remove_sources(self):
        """Runner cleanup revokes peers atomically, even when only one entry failed."""
        self._source_unavailable = True
        self._source_starting = False
        self._source_entries.clear()
        if not self.closed:
            # Constructor cleanup can run before assignment. In that case the
            # loading branch logs the original error once construction unwinds.
            if self._source_runner is not None:
                self._reject_sources(self._source_runner.sandbox.failure or "Shared source runner was closed")
        self._remove_mods(self._sources)
        if not self.closed and not self._regulation_dispatching and not self._ui_rejecting:
            self._progress("complete" if self.finished else "loading")

    def _reject_mods(self, owners, detail):
        """Reject selected independent components or their entire shared source group."""
        failure = None
        for owner in sorted(owners):
            mod = self.mods.get(owner)
            if mod is None:
                continue  # Closing a source peer may already have removed this member.
            if type(mod) is SharedSourceMod:
                self._reject_sources(detail)
            else:
                self.failed += 1
                self._log("REJECTED", level="WARNING", id=owner, detail=detail)
            try:
                mod.close()
            except Exception as error:
                failure = failure or error
                self._log("CLEANUP ERROR", level="ERROR", id=owner, detail=str(error)[:512], error=error)
        if failure is not None:
            raise failure

    def _publish_ui(self):
        """Handle immediate and deferred native rejection with the same policy."""
        try:
            self._flush_ui()
        except Exception as error:
            if not self.ui.failed:
                raise
            self._log("UI PUBLICATION FAILED", level="ERROR", error=error)
            self._ui_failures(self.ui.failed)

    def _ui_failure(self, area, status):
        return self._ui_failures({area: status})

    def _ui_failures(self, failures):
        rejected = {area: status for area, status in failures.items() if self.ui.reject(area, status)}
        if not rejected:
            return False
        owners = set()
        for area in rejected:
            if area == "menu":
                owners.update(owner for owner, mod in self.mods.items() if "main-menu" in mod.manifest.capabilities)
            else:
                registry = self.settings if area == "settings" else self.themes
                owners.update(record["owner"] for record in registry.records)
        # Remove every failed area's declarations before sending a replacement.
        # Otherwise closing one owner could resend another area's known failure.
        self._ui_rejecting = True
        try:
            detail = next(iter(rejected.values())).get("detail", "Native UI unavailable")
            self._reject_mods(owners, detail)
        finally:
            self._ui_rejecting = False
        self._flush_ui()
        self._progress("complete" if self.finished else "loading")
        return True

    def themes_status(self, status):
        self._ui_failure("themes", status)

    def settings_status(self, status):
        """Persist one user save, acknowledge it once, then notify its sandboxed owner."""
        if status.get("generation") != self.ui.generations["settings"]:
            return
        if self._ui_failure("settings", status):
            return
        request = status.get("save")
        if request is None:
            return
        if (type(request) is not dict or set(request) != {"id", "owner", "schema", "values"}
                or type(request["id"]) is not int or not 0 < request["id"] <= 2**53 - 1
                or type(request["owner"]) is not str):
            raise ValueError("Invalid native settings request")
        if request["id"] <= self.last_settings_request:
            self._flush_settings()
            return
        self.last_settings_request = request["id"]
        owner = request["owner"]
        mod = self.mods.get(owner)
        success, values = False, None
        try:
            if mod is None or "ui-settings" not in mod.manifest.capabilities:
                raise ValueError("Settings owner is no longer active")
            payload = json.dumps({"schema": request["schema"], "values": request["values"]}, separators=(",", ":"))
            values = json.loads(self.settings.call(owner, "save-settings", payload))
            success = True
        except (OSError, ValueError, KeyError) as error:
            self._log("SETTINGS SAVE FAILED", level="ERROR", id=owner, detail=str(error)[:512], error=error)
        self.settings_result = {"id": request["id"], "owner": owner, "success": success}
        self._flush_settings()
        if success:
            try:
                mod.event("ui-settings.changed", values)
                self._flush_ui(("settings", "themes"))
            except Exception as error:
                self._log("SETTINGS EVENT FAILED", level="ERROR", id=owner, detail=str(error)[:512], error=error)
                if owner in self.mods:
                    self.mods.pop(owner).close()

    def _log(self, event, *, level="INFO", error=None, **fields):
        """Keep paths, errors and mod messages inside one quoted log record."""
        # A long owner list must not push the reason beyond the record limit.
        if "detail" in fields:
            fields = {"detail": fields["detail"], **fields}
        self.submit_log(f"CODE MOD {event}: {json.dumps(fields, ensure_ascii=True)}", level=level, error=error)

    def _folder(self, pack):
        """Check the discovered record against our installation, not an arbitrary path."""
        if (
            type(pack) is not dict
            or pack.get("state") != "ready"
            or pack.get("series") != "formula"
        ):
            raise ManifestError("No supported Formula startup pack is available")
        name = pack.get("id")
        if type(name) is not str or len(name) > 128 or safe_name(name).name != name:
            raise ManifestError("Expected one data-pack folder name")
        folder = local_path(self.game_root, f"data/{name}")
        # Reject linked parents and a record that disagrees with the calculated folder.
        if not folder.is_dir() or pack.get("root") != str(folder.resolve(strict=True)):
            raise ManifestError("Startup pack does not match its installed data folder")
        return folder

    def _candidates(self, folder):
        """Find direct manifest-bearing directories without importing or scanning their contents."""
        candidates = []
        with os.scandir(folder) as entries:
            for count, entry in enumerate(entries, 1):
                self._check_process()
                if count > MAX_PACK_ENTRIES:
                    raise ManifestError("Data pack exceeds the discovery entry limit")
                path = Path(entry.path)
                # Do not follow a linked child, including Windows directory junctions.
                if not entry.is_dir(follow_symlinks=False) or path.is_junction():
                    continue
                manifest = path / "mod.toml"
                if manifest.exists() or manifest.is_symlink():
                    candidates.append(path)
                    if len(candidates) > MAX_MODS:
                        raise ManifestError("Data pack exceeds the 32-code-mod limit")
        return sorted(candidates)

    def _progress(self, state="loading", *, error=None):
        """Publish counts, preserving an already-recorded rejection if delivery fails."""
        if state in ("loading", "complete") and self._regulation_waiting():
            failed = self._regulation_failed
            # The native preload adapter protects the selected career after
            # the menu opens, without holding pack-owned UI for career activation.
            state = "error" if failed else "complete" if self.finished and self._regulation_armed else "loading"
        if self.set_loading is not None:
            try:
                self.set_loading(
                    dict(
                        state=state,
                        loaded=len(self.mods),
                        failed=self.failed,
                        total=self.total,
                    )
                )
                return
            except Exception as progress_error:
                if error is None:
                    raise
                self._log("PROGRESS FAILED", level="ERROR", error=progress_error, detail=str(progress_error)[:512])
                error.add_note("Loading progress could not be updated:\n" + error_text(progress_error))
            # Leave the secondary exception handler before re-raising, so the
            # primary failure keeps its original implicit context and cause.
            raise error

    def _manifest(self, path):
        if self.runner_bundle is None:
            manifest = Manifest.read(path)
            return manifest.root, manifest
        root, content = read_manifest(path)
        schema = tomllib.loads(content.decode("utf-8")).get("schema")
        if type(schema) is not int or schema not in (1, 2):
            raise ManifestError("Unsupported mod manifest schema")
        if schema == 2:
            manifest = SourceManifest.parse(content)
        else:
            manifest = Manifest.parse(root, content)
            if manifest.runtime == "python":
                raise ManifestError("Python source loading requires manifest schema 2")
        return root, manifest

    def _executor(self):
        if self.executor is None:
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mod-compiler")
        return self.executor

    def load(self, pack):
        """Start one immutable pack; completion events resume its preparation."""
        if self.started or self.closed:
            return
        self.started = True
        self.loading = self._load(pack)
        self.poll()

    def _check_process(self):
        """Stop at work boundaries; worker reads/compiles retain their own cleanup."""
        cancellation = getattr(self._bridge, "cancellation", None)
        if cancellation is not None:
            cancellation.check()

    def poll(self):
        """Advance only ready work on the owning controller thread; never wait on a compiler."""
        if self.closed:
            return
        self._check_process()
        self._start_regulations()
        if self.regulations is not None:
            # This runs on the controller/Store owner before any synchronous UI
            # bridge request. The I/O thread only wakes this loop and queues data.
            self.regulations.poll()
            status = self.regulations.status
            if status != self._regulation_status:
                self._regulation_status = status
                self._publish_ui()
                self._progress("complete" if self.finished else "loading")
        if self.loading is None:
            if not self._regulation_dispatching:
                self._publish_ui()
            return
        try:
            next(self.loading)
        except StopIteration:
            self.loading = None
        except Exception as error:
            # Retain the primary failure locally. A second progress request to
            # the same bridge can time out again or replace the original error.
            self._log("LOADING FAILED", level="ERROR", error=error, detail=str(error)[:512])
            raise
        self._start_regulations()

    def _load_sources(self):
        """Prepare one fixed source group; only this controller creates its Store."""
        with activity_scope(self.submit_log, "mod_loading", owners=sorted(self._sources)):
            timings = {}
            try:
                members = tuple((item.root, item.manifest) for item in self._sources.values())
                with SourcePreparation(self._executor(), self.runner_bundle, members,
                                       cache=self.cache, wake=self._ready) as job:
                    result = None
                    with measure(timings, "source_prepare"):
                        while result is None:
                            result = job.take()
                            if result is None:
                                yield
                with result:
                    self._check_process()
                    timings.update(result.timings)
                    if result.compiled.cache_status is not None:
                        self._log("CACHE", owners=list(self._sources), **result.compiled.cache_status)
                    providers = {}
                    for owner, candidate in self._sources.items():
                        if "ui-settings" in candidate.manifest.capabilities:
                            self.settings.bind(owner, candidate.root)
                        operations = _host_operations(candidate.manifest, self._submit_mod_log,
                                                      self.menu.add if self.ui.send is not None else None,
                                                      self.settings.call, self.themes.register)
                        providers[owner] = {name: operation.callback for name, operation in operations.items()}
                    # Every peer's files are available before the first import. Entry
                    # imports happen together; activation below keeps pack ID order.
                    with measure(timings, "source_load"):
                        self._source_runner = SharedPythonRunner(self.runner_bundle, result.snapshots, providers,
                                                                 cleanup=self._remove_sources,
                                                                 prepared=result.compiled, timings=timings)
                    self._source_entries = {snapshot.manifest.id: SharedSourceMod(self._source_runner, snapshot)
                                            for snapshot in result.snapshots}
            finally:
                self._log("SOURCE GROUP TIMING", level="DEBUG", owners=list(self._sources), phase_seconds=timings)

    def _load(self, pack):
        """Keep guest calls serial while yielding during worker preparation."""
        started = perf_counter()
        load_timings = {}
        try:
            with measure(load_timings, "discovery"):
                folder = self._folder(pack)
                candidates = self._candidates(folder)
                self._pack_root = folder
        except (OSError, ValueError, RuntimeError) as error:
            self._log(
                "DISCOVERY REJECTED", level="WARNING",
                detail=str(error)[:512],
                error=error,
            )
            self._log("DISCOVERY TIMING", level="DEBUG", phase_seconds=load_timings,
                      total_seconds=round(perf_counter() - started, 6))
            self._progress("error", error=error)
            return
        self.total = len(candidates)
        self._progress()
        candidates_read = []
        for path in candidates:
            timings = {}
            try:
                with measure(timings, "manifest"), activity_scope(self.submit_log, "mod_loading", folder=path.name):
                    root, manifest = self._manifest(path)
                    # Recheck after resolution in case a parent changed during discovery.
                    if root.parent != folder:
                        raise ManifestError(
                            "Code mod is not a direct child of the selected pack"
                        )
                candidates_read.append(PackCandidate(root, manifest, timings))
            except (OSError, ValueError, RuntimeError) as error:
                self.failed += 1
                self._log(
                    "REJECTED", level="WARNING",
                    folder=path.name,
                    detail=str(error)[:512],
                    error=error,
                )
                self._log("TIMING", level="DEBUG", folder=path.name, phase_seconds=timings)
        counts = Counter(item.manifest.id for item in candidates_read)
        candidates_read.sort(key=lambda item: (item.manifest.id, item.root.name))
        self._sources = {item.manifest.id: item for item in candidates_read
                         if type(item.manifest) is SourceManifest and counts[item.manifest.id] == 1}
        self._source_starting = bool(self._sources)
        # Reserve before activating any UI owner: a later regulation owner must
        # fence earlier declarations too. Rejected duplicate IDs never reserve.
        self._regulation_owners = {item.manifest.id for item in candidates_read
                                  if type(item.manifest) is SourceManifest
                                  and counts[item.manifest.id] == 1
                                  and "regulations" in item.manifest.capabilities}
        self.prerequisites = Prerequisites(item for item in candidates_read if counts[item.manifest.id] == 1)
        request = self.prerequisites.request
        if request["career"] or request["modules"]:
            try:
                if self._bridge is None:
                    raise ValueError("Native prerequisites need a game bridge")
                self.readiness_status(self._bridge.request("prerequisites", prerequisites=request)["readiness"])
            except Exception as error:
                # Only registrations needing unavailable native data stay held.
                self._log("PREREQUISITES UNAVAILABLE", level="WARNING", detail=str(error)[:512], error=error)
        for candidate in candidates_read:
            self._check_process()
            manifest, timings = candidate.manifest, candidate.timings
            # Decide duplicate identity before initializing either copy; never pick a winner.
            if counts[manifest.id] > 1:
                self.failed += 1
                self._progress()
                self._log(
                    "REJECTED", level="WARNING",
                    id=manifest.id,
                    folder=candidate.root.name,
                    detail="Duplicate mod ID",
                )
                self._log("TIMING", level="DEBUG", id=manifest.id, folder=candidate.root.name, phase_seconds=timings)
                continue
            if type(manifest) is SourceManifest and self._source_unavailable:
                continue  # The fixed group's failure already counted and logged every member.
            prepared = None
            load_error = None
            try:
                with activity_scope(self.submit_log, "mod_loading", mod=manifest.id):
                    if type(manifest) is SourceManifest:
                        if self._source_runner is None:
                            yield from self._load_sources()
                        mod = self._source_entries[manifest.id]
                    elif self.background:
                        self.pending = self._executor().submit(_read_component, manifest, timings)
                        self.pending.add_done_callback(self._ready)
                        while not self.pending.done():
                            yield
                        future = self.pending
                        self.pending = None
                        prepared = CompiledMod(future.result(), timings, cache=self.cache, executor=self._executor())
                        prepared.when_ready(self._ready)
                        while not prepared.poll():
                            yield
                    elif self.cache is not None:
                        prepared = CompiledMod(_read_component(manifest, timings), timings, cache=self.cache)
                    if prepared is not None and prepared.cache_status is not None:
                        self._log("CACHE", id=manifest.id, **prepared.cache_status)
                    if type(manifest) is not SourceManifest:
                        if "ui-settings" in manifest.capabilities:
                            self.settings.bind(manifest.id, candidate.root)
                        mod = Mod(manifest, submit_log=self._submit_mod_log,
                                  menu=self.menu.add if self.ui.send is not None else None,
                                  settings=self.settings.call, theme=self.themes.register,
                                  cleanup=lambda owner=manifest.id: self._remove_mod(owner),
                                  timings=timings, prepared=prepared)
                    # Retain ownership before activation so interruption can still close this instance.
                    self.mods[manifest.id] = mod
                    self._check_process()
                    with measure(timings, "activate"):
                        mod.activate()
                    self._check_process()
                    areas = _ui_areas(manifest.capabilities)
                    if areas:
                        with measure(timings, "registration"):
                            self._flush_ui(areas)
                    self._log("ACTIVE", id=manifest.id, version=manifest.version)
            except Exception as error:
                load_error = error
                if type(manifest) is SourceManifest:
                    self._reject_sources(error)
                else:
                    self.failed += 1
                    self._log("REJECTED", level="WARNING" if isinstance(error, ManifestError) else "ERROR",
                              id=manifest.id, folder=candidate.root.name, detail=str(error)[:512], error=error)
                # Record the rejection before cleanup can fail. A cleanup error
                # makes loading fatal, but cannot become its primary reason.
                cleanup_failed = False
                try:
                    if type(manifest) is SourceManifest:
                        if self._source_runner is not None:
                            self._source_runner.close()
                        else:
                            self._remove_sources()
                    else:
                        failed = self.mods.get(manifest.id)
                        if failed is not None:
                            failed.close()
                        self._remove_mod(manifest.id)
                except Exception as cleanup_error:
                    self._log("CLEANUP ERROR", level="ERROR", id=manifest.id, error=cleanup_error,
                              detail=str(cleanup_error)[:512])
                    error.add_note("Code mod cleanup failed:\n" + error_text(cleanup_error))
                    cleanup_failed = True
                if cleanup_failed:
                    raise
                areas = _ui_areas(manifest.capabilities)
                if areas:
                    try:
                        self._flush_ui(areas)
                    except Exception as cleanup_error:
                        self._log("CLEANUP ERROR", level="ERROR", id=manifest.id, detail=str(cleanup_error)[:512], error=cleanup_error)
            finally:
                if prepared is not None:
                    prepared.close()  # An adopted result has already transferred its resources.
                # Log once per attempted mod, after execution and cleanup return.
                self._log(
                    "TIMING", level="DEBUG", pack=folder.name, id=manifest.id, phase_seconds=timings
                )
            self._progress(error=load_error)
        self.finished = True
        self._source_starting = False
        self._source_entries.clear()
        # A failed source group never releases required rule reservations. Only
        # an independent surviving component may publish when no rules are lost.
        self._publish_ui()
        self._progress("complete")
        self._log(
            "BINDING REQUIRED" if self._regulation_waiting() else "READY",
            pack=pack["id"],
            active=len(self.mods),
        )
        self._log(
            "PACK TIMING", level="DEBUG", pack=pack["id"],
            phase_seconds=load_timings,
            total_seconds=round(perf_counter() - started, 6),
        )

    def _submit_mod_log(self, mod_id, message):
        """Submit a mod message with its host-owned identity."""
        self._log("REPORT", id=mod_id, message=message)

    def menu_status(self, status):
        """Keep menu diagnostics and use the common UI rejection policy."""
        if status.get("generation") != self.ui.generations["menu"]:
            return
        self._log("MENU", level="ERROR" if status.get("state") == "failed" else "DEBUG", **status)
        self._ui_failure("menu", status)

    def close(self):
        """Close in reverse activation order, attempting every instance even if cleanup fails."""
        if self.closed:
            return
        self.closed = True
        if self.pending is not None:
            if self.pending.running():
                self._log(
                    "READ DRAINING",
                    detail="Guest loading cancelled; discarding component bytes when the read returns",
                )
            self.pending.cancel()
            self.pending = None
        if self.loading is not None:
            self.loading.close()
            self.loading = None
        if self.executor is not None:
            # Read jobs and this scope's code requests were cancelled individually.
            # Another session may still need a compilation queued on this executor;
            # only the registry's last requester can cancel that shared job.
            self.executor.shutdown(wait=False)
        failed = False
        if self.regulations is not None:
            try:
                self.regulations.close()
            except Exception as error:
                failed = True
                self._log("CLEANUP ERROR", level="ERROR", detail=str(error)[:512], error=error)
        # Cleanup may remove shared peers from self.mods. Keep the activation
        # snapshot so every logical owner still gets its reverse-order close log.
        owned = list(self.mods.items())
        self.mods.clear()
        for mod_id, mod in reversed(owned):
            try:
                mod.close()
            except Exception as error:
                failed = True
                self._log("CLEANUP ERROR", level="ERROR", id=mod_id, detail=str(error)[:512], error=error)
            else:
                self._log("CLOSED", id=mod_id)
        if self._source_runner is not None:
            try:
                self._source_runner.close()  # Also owns entries never reached by activation.
            except Exception as error:
                failed = True
                self._log("CLEANUP ERROR", level="ERROR", detail=str(error)[:512], error=error)
        self._source_entries.clear()
        try:
            self._flush_ui()
        except Exception as error:
            failed = True
            self._log("CLEANUP ERROR", level="ERROR", detail=str(error)[:512], error=error)
        if failed:
            raise RuntimeError("Code mod cleanup failed; see the launcher log")
