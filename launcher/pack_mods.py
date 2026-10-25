# SPDX-License-Identifier: MPL-2.0
"""Discover and own sandboxed code mods for one process's selected Formula pack."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from time import perf_counter
import tomllib

from launcher.bundled_dependencies import local_path, safe_name
from launcher.load_timing import measure
from launcher.compiled_mod import CompiledMod
from launcher.mod_contract import Manifest, ManifestError, Mod, _host_operations
from launcher.pack_candidate import PackCandidate
from launcher.shared_source_mod import SharedSourceMod
from launcher.ui_settings import SettingsRequests
from hook_manager.menu import MenuRequests
from hook_manager.themes import ThemeRequests
from mod_runtime.bundle import RunnerBundle
from mod_runtime.preparation import SourcePreparation
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest, read_manifest

# Bound discovery and the number of mods admitted by one game process.
MAX_PACK_ENTRIES = 4096
MAX_MODS = 32


def _read_component(manifest, timings):
    """Read bounded portable bytes; compilation is coordinated by the controller."""
    with measure(timings, "component_read"):
        return manifest.read_component()


class PackMods:
    """Load once on the controller thread; close all instances with their game session."""

    def __init__(
        self,
        game_root,
        report,
        *,
        set_menu=None,
        set_settings=None,
        set_themes=None,
        set_loading=None,
        background=False,
        cache=None,
        runner_bundle=None,
        wake=None,
        bridge=None,
    ):
        # game_root and report come from the launcher, never from a mod manifest.
        self.game_root = Path(game_root).resolve(strict=True)
        self.report = report
        self.mods = {}
        self.started = False
        self.closed = False
        self.menu = MenuRequests()
        self.settings = SettingsRequests()
        self.set_settings = set_settings
        self.applied_settings = []
        self.settings_generation = self.last_settings_request = 0
        self.settings_result = None
        self.themes = ThemeRequests()
        self.set_themes = set_themes
        self.applied_themes = []
        self.themes_generation = 0
        self.set_menu = set_menu
        self.applied_menu = []
        self.menu_generation = 0
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
        self._regulation_failed = False
        self._regulation_dispatching = False
        self.regulations, self._regulation_status = None, None
        self._regulation_owners, self._regulation_wake = set(), wake
        self._bridge = bridge
        self._regulation_armed = False
        self._regulation_generation = None
        self._regulation_retired = True

    def _start_regulations(self):
        """Bind once, then protect native loading before enabling career selection.

        Menu readiness cannot wait for a career that the user has yet to select.
        Mod UI still waits for that career's completed regulation activation.
        """
        if (self._bridge is None or not self.finished or not self._regulation_owners
                or self._regulations_prepared or self._regulation_failed):
            return
        from launcher.regulations.runtime import SCOPE_COMPONENTS

        self.bind_regulations(scopes=set(SCOPE_COMPONENTS))
        if self._regulation_owners:
            categories = self.regulations.definitions.custom_categories
            # Only enabled, bound owners reach this fixed data-only operation.
            # Native policy and progress continue through their own loaders.
            data = {"regulations": {"pack": str(self._pack_root), "categories": categories}} if categories else {}
            scripts = self.regulations.definitions.custom_scripts
            if scripts:
                data["regulations"]["scripted_changes"] = scripts
            status = self._bridge.request("await-regulations", **data)
            if status.get("adapters", {}).get("career_preload", {}).get("state") != "armed":
                raise RuntimeError("Native regulation loading protection was not confirmed")
            self._regulation_armed = True
        self._progress("complete")

    def career_preload(self, status):
        """Retire departed careers and prepare the next native-captured attempt.

        The controller delivers changes on its Store-owning thread. A repeated
        waiting notice must not publish again or install a second set of hooks.
        """
        if self.closed or not self._regulation_armed or self._regulation_failed:
            return
        if status.get("state") == "unavailable":
            raise RuntimeError("Native regulation loading protection became unavailable")
        if status.get("state") not in ("waiting", "left"):
            return
        generation = status.get("generation")
        if type(generation) is not int or generation < 1:
            raise ValueError("Invalid native regulation loading attempt")
        if self._regulation_generation is not None:
            if generation < self._regulation_generation or generation == self._regulation_generation and status["state"] != "left":
                return
            if not self._regulation_retired:
                self._regulation_dispatching = True
                try:
                    self._bridge.retire_regulations(self.regulations, self._regulation_generation)
                    self._regulation_retired = True
                    self._regulation_status = None  # The next career must republish its held UI.
                finally:
                    self._regulation_dispatching = False
        if status["state"] == "left":
            # Keep the number after departure so a delayed waiting notice
            # cannot restart a cancelled attempt. A retry gets a new number.
            self._regulation_generation = generation
            self._publish_ui()
            return
        self._regulation_generation = generation
        self._regulation_retired = False
        prepared = self.prepare_native_regulations(self._bridge, generation)
        if self._regulation_failed:
            raise RuntimeError("Native regulation preparation was not confirmed")
        if prepared.get("adapters", {}).get("regulations", {}).get("state") != "prepared":
            return  # A cancelled/replaced loading job did not acquire a policy.
        self.activate_native_regulations(self._bridge, generation)

    def _regulation_waiting(self):
        return (not self.closed and (self._regulation_failed or (bool(self._regulation_owners)
                and (self.regulations is None or not self.regulations.ready))))

    def _ui_waiting(self):
        # Imports can register UI for members not yet activated. Publish only
        # after pack startup, or revoke everything if their shared runner fails.
        return (not self.closed and self._source_starting) or self._regulation_waiting()

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
            self.regulations = RegulationRuntime(definitions, self.mods, self._regulation_wake)
            self.regulations.bind()
        except Exception as error:
            # Preparation can fail before there is a runtime, and listener startup
            # can fail after handlers bind. Neither can revert to loading/ready.
            self._regulation_failed = True
            self._log("REGULATIONS FAILED", owners=sorted(self._regulation_owners), detail=str(error)[:512])
            try:
                self._reject_mods(self._regulation_owners, str(error)[:512])
            except Exception as cleanup_error:
                self._log("CLEANUP ERROR", detail=str(cleanup_error)[:512])
            self._progress("error")
            raise
        if self.regulations.ready:
            self._publish_ui()
            self._progress("complete")
        return self.regulations

    def prepare_native_regulations(self, bridge, generation):
        """Send the loaded career's rule data before installing its game hooks.

        Store cleanup can remove mod UI even when only transport is polled.
        Defer those updates while the game is waiting on this bridge request;
        they may use the bridge again only after its reply has arrived.
        """
        if (self.closed or self._regulation_failed or self.regulations is None
                or self._regulation_dispatching):
            raise RuntimeError("Regulations are not available for native preparation")
        self._regulation_dispatching = True
        try:
            status = bridge.prepare_regulations(self.regulations, generation)
        except BaseException:
            self._regulation_failed = True
            self._regulation_status = None  # Refresh deferred UI on the next safe poll.
            raise
        finally:
            self._regulation_dispatching = False
        if status.get("adapters", {}).get("regulations", {}).get("state") != "prepared":
            preload = status.get("adapters", {}).get("career_preload", {})
            if preload.get("state") not in ("left", "waiting") or preload.get("generation") == generation and preload.get("state") != "left":
                self._regulation_failed = True
                self.regulations.close()
        self._publish_ui()
        self._progress("complete")
        return status

    def activate_native_regulations(self, bridge, generation):
        """Keep UI held until prepared hooks are installed, checked and enabled."""
        if (self.closed or self._regulation_failed or self.regulations is None
                or self._regulation_dispatching):
            raise RuntimeError("Regulations are not available for native activation")
        self._regulation_dispatching = True
        try:
            status = bridge.activate_regulations(self.regulations, generation)
        except BaseException:
            self._regulation_failed = True
            self._regulation_status = None
            raise
        finally:
            self._regulation_dispatching = False
        self.poll()
        return status

    def confirm_regulations(self, components):
        """Check trusted installation coverage before releasing the pack's UI."""
        if self._regulation_failed or self.regulations is None:
            raise RuntimeError("Regulations have not bound")
        try:
            self.regulations.confirm_native(components)
        finally:
            # An incomplete replacement report revokes approval as well. Keep
            # published UI and loading status in step with that change immediately.
            self._publish_ui()
            self._progress("complete")

    def prepare_regulations(self, *, scopes, native_categories=()):
        """Prepare once from admitted active source owners, without publishing rules.

        Public API versions cannot request regulations. Private regulation
        owners must bind and become ready before their pack publishes resources.
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
        return RegulationDefinitions.read(self._pack_root, sources, scopes=scopes,
                                          native_categories=native_categories)

    def _flush_menu(self):
        """Send staged declarations outside guest execution, without per-frame callbacks."""
        # Keep desired declarations staged, but revoke published UI if a required
        # owner fails. Readiness must block additions without blocking cleanup.
        records = [] if self._ui_waiting() else self.menu.buttons
        if self.set_menu is None or self.applied_menu == records:
            return
        # An uncertain acknowledgement must still cause a later rollback/cleanup request.
        self.applied_menu = None
        result = self.set_menu(records)
        if result is not None:
            self.menu_generation = result["menu"]["generation"]
            if result["menu"]["state"] == "failed":
                raise ManifestError(result["menu"]["detail"])
        self.applied_menu = [dict(item) for item in records]

    def _remove_menu(self, owner):
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
        error = None
        for applied, flush in ((self.applied_settings, self._flush_settings), (self.applied_menu, self._flush_menu),
                               (self.applied_themes, self._flush_themes)):
            # Validation holds the native caller until its reply. A synchronous
            # UI bridge request here would wait on that same game thread.
            if (not self.closed and not self._regulation_dispatching
                    and (applied is None or any(item["owner"] in owners for item in applied))):
                try:
                    flush()
                except Exception as failure:
                    error = error or failure
        if error is not None:
            raise error

    def _reject_sources(self, detail):
        """Count and identify the entire failed group once, including unactivated peers."""
        self._source_unavailable = True
        self._source_starting = False
        if self._regulation_owners:
            self._regulation_failed = True
        if self.closed or self._source_reported:
            return
        self._source_reported = True
        self.failed += len(self._sources)
        detail = str(detail)[:512]
        self._log("SOURCE GROUP FAILED", owners=list(self._sources), detail=detail)
        for owner, candidate in self._sources.items():
            self._log("REJECTED", id=owner, folder=candidate.root.name, detail=detail)

    def _remove_sources(self):
        """Runner cleanup revokes peers atomically, even when only one entry failed."""
        self._source_unavailable = True
        self._source_starting = False
        self._source_entries.clear()
        if not self.closed:
            if self._regulation_owners:
                self._regulation_failed = True
            # Constructor cleanup can run before assignment. In that case the
            # loading branch logs the original error once construction unwinds.
            if self._source_runner is not None:
                self._reject_sources(self._source_runner.sandbox.failure or "Shared source runner was closed")
        self._remove_mods(self._sources)
        if not self.closed and not self._regulation_dispatching:
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
                self._log("REJECTED", id=owner, detail=detail)
            try:
                mod.close()
            except Exception as error:
                failure = failure or error
                self._log("CLEANUP ERROR", id=owner, detail=str(error)[:512])
        if failure is not None:
            raise failure

    def _publish_ui(self):
        """Publish staged UI, treating immediate and deferred rejection alike."""
        for area, flush, status in (("menu", self._flush_menu, self.menu_status),
                                    ("settings", self._flush_settings, self.settings_status),
                                    ("themes", self._flush_themes, self.themes_status)):
            try:
                flush()
            except Exception as error:
                status({"generation": getattr(self, area + "_generation"),
                        "state": "failed", "detail": str(error)[:512]})

    def _flush_settings(self):
        """Publish copied declarations only after guest execution has returned."""
        waiting = self._ui_waiting()
        records = [] if waiting else self.settings.records
        if waiting:
            self.settings_result = None  # A revoked registry cannot accept a stale save acknowledgement.
        if self.set_settings is None or (self.applied_settings == records and self.settings_result is None):
            return
        self.applied_settings = None
        result = self.set_settings(records, self.settings_result)
        if result is not None:
            self.settings_generation = result["settings"]["generation"]
            if result["settings"]["state"] == "failed":
                raise ManifestError(result["settings"]["detail"])
        self.applied_settings = records
        self.settings_result = None

    def _flush_themes(self):
        records = [] if self._ui_waiting() else self.themes.records
        if self.set_themes is None or self.applied_themes == records:
            return
        self.applied_themes = None
        result = self.set_themes(records)
        if result is not None:
            self.themes_generation = result["themes"]["generation"]
            if result["themes"]["state"] == "failed":
                raise ManifestError(result["themes"]["detail"])
        self.applied_themes = records

    def themes_status(self, status):
        if status.get("generation") != self.themes_generation or status.get("state") != "failed":
            return
        self.applied_themes = None
        owners = {record["owner"] for record in self.themes.records}
        self._reject_mods(owners, status.get("detail", "Themes unavailable"))
        self._flush_themes()
        self._progress("complete" if self.finished else "loading")

    def settings_status(self, status):
        """Persist one user save, acknowledge it once, then notify its sandboxed owner."""
        if status.get("generation") != self.settings_generation:
            return
        if status.get("state") == "failed":
            self.applied_settings = None
            owners = {record["owner"] for record in self.settings.records}
            self._reject_mods(owners, status.get("detail", "Settings unavailable"))
            self._flush_settings()
            self._progress("complete" if self.finished else "loading")
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
            self._log("SETTINGS SAVE FAILED", id=owner, detail=str(error)[:512])
        self.settings_result = {"id": request["id"], "owner": owner, "success": success}
        self._flush_settings()
        if success:
            try:
                mod.event("ui-settings.changed", values)
                self._flush_settings()
                self._flush_themes()
            except Exception as error:
                self._log("SETTINGS EVENT FAILED", id=owner, detail=str(error)[:512])
                if owner in self.mods:
                    self.mods.pop(owner).close()

    def _log(self, event, **fields):
        """Keep paths, errors and mod messages inside one quoted log record."""
        self.report(f"CODE MOD {event}: {json.dumps(fields, ensure_ascii=True)}")

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

    def _progress(self, state="loading"):
        """Publish host-owned counts; native readiness still waits for registry installation."""
        if state in ("loading", "complete") and self._regulation_waiting():
            failed = self._regulation_failed or (self.regulations is not None
                     and "unavailable" in self.regulations.status["owners"].values())
            # The native preload adapter protects the selected career after
            # the menu opens. Required mod UI remains held by _ui_waiting().
            state = "error" if failed else "complete" if self.finished and self._regulation_armed else "loading"
        if self.set_loading is not None:
            self.set_loading(
                dict(
                    state=state,
                    loaded=len(self.mods),
                    failed=self.failed,
                    total=self.total,
                )
            )

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
        """Start one immutable pack; normal launches advance preparations through poll()."""
        if self.started or self.closed:
            return
        self.started = True
        self.loading = self._load(pack)
        self.poll()

    def poll(self):
        """Advance only ready work on the owning controller thread; never wait on a compiler."""
        if self.closed:
            return
        self._start_regulations()
        if self.regulations is not None:
            # This runs on the controller/Store owner before any synchronous UI
            # bridge request. The I/O thread only wakes this loop and queues data.
            self._regulation_dispatching = True
            try:
                self.regulations.poll()
            finally:
                self._regulation_dispatching = False
            status = self.regulations.status
            if (self._bridge is not None and self._regulation_generation is not None
                    and not self.regulations.closed and "unavailable" in status["owners"].values()
                    and any(not enabled for values in self.regulations.definitions.native_rules.values()
                            for enabled in values.values())):
                # Close transport before bridge work to release waiting guards.
                # Keep those guards installed: failed custom rules cannot become
                # permission to sign. Only native bypass helpers are restored.
                self._regulation_failed = self._regulation_dispatching = True
                try:
                    self._bridge.restore_native_rules(self.regulations, self._regulation_generation)
                finally:
                    self._regulation_dispatching = False
                status = self.regulations.status
            if status != self._regulation_status:
                self._regulation_status = status
                self._publish_ui()
                self._progress("complete" if self.finished else "loading")
        if self.loading is None:
            self._flush_settings()
            self._flush_themes()
            return
        try:
            next(self.loading)
        except StopIteration:
            self.loading = None
        except BaseException:
            self._progress("error")
            raise

    def _load_sources(self):
        """Prepare one fixed source group; only this controller creates its Store."""
        timings = {}
        try:
            members = tuple((item.root, item.manifest) for item in self._sources.values())
            with SourcePreparation(self._executor(), self.runner_bundle, members, cache=self.cache) as job:
                result = None
                with measure(timings, "source_prepare"):
                    while result is None:
                        result = job.take()
                        if result is None:
                            yield
            with result:
                timings.update(result.timings)
                if result.compiled.cache_status is not None:
                    self._log("CACHE", owners=list(self._sources), **result.compiled.cache_status)
                providers = {}
                for owner, candidate in self._sources.items():
                    if "ui-settings" in candidate.manifest.capabilities:
                        self.settings.bind(owner, candidate.root)
                    operations = _host_operations(candidate.manifest, self._diagnostic,
                                                  self.menu.add if self.set_menu is not None else None,
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
            self._log("SOURCE GROUP TIMING", owners=list(self._sources), phase_seconds=timings)

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
                "DISCOVERY REJECTED",
                detail=str(error)[:512],
                phase_seconds=load_timings,
                total_seconds=round(perf_counter() - started, 6),
            )
            self._progress("error")
            return
        self.total = len(candidates)
        self._progress()
        candidates_read = []
        for path in candidates:
            timings = {}
            try:
                with measure(timings, "manifest"):
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
                    "REJECTED",
                    folder=path.name,
                    detail=str(error)[:512],
                    phase_seconds=timings,
                )
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
        for candidate in candidates_read:
            manifest, timings = candidate.manifest, candidate.timings
            # Decide duplicate identity before initializing either copy; never pick a winner.
            if counts[manifest.id] > 1:
                self.failed += 1
                self._progress()
                self._log(
                    "REJECTED",
                    id=manifest.id,
                    folder=candidate.root.name,
                    detail="Duplicate mod ID",
                    phase_seconds=timings,
                )
                continue
            if type(manifest) is SourceManifest and self._source_unavailable:
                continue  # The fixed group's failure already counted and logged every member.
            prepared = None
            try:
                if type(manifest) is SourceManifest:
                    if self._source_runner is None:
                        yield from self._load_sources()
                    mod = self._source_entries[manifest.id]
                elif self.background:
                    self.pending = self._executor().submit(_read_component, manifest, timings)
                    while not self.pending.done():
                        yield
                    future = self.pending
                    self.pending = None
                    prepared = CompiledMod(future.result(), timings, cache=self.cache, executor=self._executor())
                    while not prepared.poll():
                        yield
                elif self.cache is not None:
                    prepared = CompiledMod(_read_component(manifest, timings), timings, cache=self.cache)
                if prepared is not None and prepared.cache_status is not None:
                    self._log("CACHE", id=manifest.id, **prepared.cache_status)
                if type(manifest) is not SourceManifest:
                    if "ui-settings" in manifest.capabilities:
                        self.settings.bind(manifest.id, candidate.root)
                    mod = Mod(manifest, report=self._diagnostic,
                              menu=self.menu.add if self.set_menu is not None else None,
                              settings=self.settings.call, theme=self.themes.register,
                              cleanup=lambda owner=manifest.id: self._remove_menu(owner),
                              timings=timings, prepared=prepared)
                # Retain ownership before activation so interruption can still close this instance.
                self.mods[manifest.id] = mod
                with measure(timings, "activate"):
                    mod.activate()
                if "main-menu" in manifest.capabilities:
                    with measure(timings, "registration"):
                        self._flush_menu()
                if "ui-settings" in manifest.capabilities:
                    self._flush_settings()
                    self._flush_themes()
            except Exception as error:
                if type(manifest) is SourceManifest:
                    self._reject_sources(error)
                    if self._source_runner is not None:
                        self._source_runner.close()
                    else:
                        self._remove_sources()
                else:
                    self.failed += 1
                    failed = self.mods.get(manifest.id)
                    if failed is not None:
                        failed.close()
                    self._remove_menu(manifest.id)
                    self._log("REJECTED", id=manifest.id, folder=candidate.root.name, detail=str(error)[:512])
                for capability, flush in (("main-menu", self._flush_menu), ("ui-settings", self._flush_settings),
                                          ("ui-settings", self._flush_themes)):
                    if capability in manifest.capabilities:
                        try:
                            flush()
                        except Exception as cleanup_error:
                            self._log("CLEANUP ERROR", id=manifest.id, detail=str(cleanup_error)[:512])
            else:
                self._log("ACTIVE", id=manifest.id, version=manifest.version)
            finally:
                if prepared is not None:
                    prepared.close()  # An adopted result has already transferred its resources.
                # Emit once per attempted mod, after guest execution and cleanup have returned.
                self._log(
                    "TIMING", pack=folder.name, id=manifest.id, phase_seconds=timings
                )
            self._progress()
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
            phase_seconds=load_timings,
            total_seconds=round(perf_counter() - started, 6),
        )

    def _diagnostic(self, mod_id, message):
        """Route the approved report operation to the launcher log with host-owned identity."""
        self._log("REPORT", id=mod_id, message=message)

    def menu_status(self, status):
        """Revoke affected components and shared peers after deferred UI failure."""
        # A reply queued before a later registry replacement cannot revoke the new registry.
        if status.get("generation") != self.menu_generation:
            return
        self._log("MENU", **status)
        if status.get("state") != "failed":
            return
        # The failed native registry must be cleared once, after all UI owners are revoked.
        self.applied_menu = None
        self._reject_mods([owner for owner, mod in self.mods.items() if "main-menu" in mod.manifest.capabilities],
                          status.get("detail", "Menu unavailable"))
        self._flush_menu()
        self._progress("complete" if self.finished else "loading")

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
                self._log("CLEANUP ERROR", detail=str(error)[:512])
        # Cleanup may remove shared peers from self.mods. Keep the activation
        # snapshot so every logical owner still gets its reverse-order close log.
        owned = list(self.mods.items())
        self.mods.clear()
        for mod_id, mod in reversed(owned):
            try:
                mod.close()
            except Exception as error:
                failed = True
                self._log("CLEANUP ERROR", id=mod_id, detail=str(error)[:512])
            else:
                self._log("CLOSED", id=mod_id)
        if self._source_runner is not None:
            try:
                self._source_runner.close()  # Also owns entries never reached by activation.
            except Exception as error:
                failed = True
                self._log("CLEANUP ERROR", detail=str(error)[:512])
        self._source_entries.clear()
        for flush in (self._flush_menu, self._flush_settings, self._flush_themes):
            try:
                flush()
            except Exception as error:
                failed = True
                self._log("CLEANUP ERROR", detail=str(error)[:512])
        if failed:
            raise RuntimeError("Code mod cleanup failed; see the launcher log")
