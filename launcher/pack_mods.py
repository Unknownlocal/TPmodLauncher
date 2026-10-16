# SPDX-License-Identifier: MPL-2.0
"""Discover and own sandboxed code mods for one process's selected Formula pack."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
from time import perf_counter
import tomllib

from launcher.bundled_dependencies import local_path, safe_name
from launcher.load_timing import measure
from launcher.compiled_mod import CompiledMod
from launcher.mod_contract import Manifest, ManifestError, Mod
from launcher.source_mod import SourceMod
from launcher.ui_settings import SettingsRequests
from hook_manager.menu import MenuRequests
from mod_runtime.bundle import RunnerBundle
from mod_runtime.preparation import SourcePreparation
from mod_runtime.source import SourceManifest, read_manifest

# Bound discovery and the number of mods admitted by one game process.
MAX_PACK_ENTRIES = 4096
MAX_MODS = 32


@dataclass
class _Candidate:
    root: Path
    manifest: Manifest | SourceManifest
    timings: dict


def _prepare(manifest, timings, cache=None):
    """Read and compile on a worker; no Store, guest entry or native registration occurs here."""
    with measure(timings, "component_read"):
        data = manifest.read_component()
    return CompiledMod(data, timings, cache=cache)


def _discard(future):
    """Release an abandoned compiler result; cancellation needs no guest or bridge work."""
    try:
        future.result().close()
    except Exception:
        pass  # Failed/cancelled preparations own no usable compiler result.


class PackMods:
    """Load once on the controller thread; close all instances with their game session."""

    def __init__(
        self,
        game_root,
        report,
        *,
        set_menu=None,
        set_loading=None,
        background=False,
        cache=None,
        runner_bundle=None,
    ):
        # game_root and report come from the launcher, never from a mod manifest.
        self.game_root = Path(game_root).resolve(strict=True)
        self.report = report
        self.mods = {}
        self.started = False
        self.closed = False
        self.menu = MenuRequests()
        self.settings = SettingsRequests()
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

    def _flush_menu(self):
        """Send staged declarations outside guest execution, without per-frame callbacks."""
        if self.set_menu is None or self.applied_menu == self.menu.buttons:
            return
        # An uncertain acknowledgement must still cause a later rollback/cleanup request.
        self.applied_menu = None
        result = self.set_menu(self.menu.buttons)
        if result is not None:
            self.menu_generation = result["menu"]["generation"]
            if result["menu"]["state"] == "failed":
                raise ManifestError(result["menu"]["detail"])
        self.applied_menu = [dict(item) for item in self.menu.buttons]

    def _remove_menu(self, owner):
        """Revoke a closed mod immediately; bulk shutdown publishes one final empty registry."""
        self.mods.pop(owner, None)
        self.menu.remove(owner)
        self.settings.remove(owner)
        if (
            not self.closed
            and self.applied_menu is not None
            and any(item["owner"] == owner for item in self.applied_menu)
        ):
            self._flush_menu()

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
        if self.loading is None or self.closed:
            return
        try:
            next(self.loading)
        except StopIteration:
            self.loading = None
        except BaseException:
            self._progress("error")
            raise

    def _load(self, pack):
        """Keep guest calls serial while yielding during worker preparation."""
        started = perf_counter()
        load_timings = {}
        try:
            with measure(load_timings, "discovery"):
                folder = self._folder(pack)
                candidates = self._candidates(folder)
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
                candidates_read.append(_Candidate(root, manifest, timings))
            except (OSError, ValueError, RuntimeError) as error:
                self.failed += 1
                self._log(
                    "REJECTED",
                    folder=path.name,
                    detail=str(error)[:512],
                    phase_seconds=timings,
                )
        counts = Counter(item.manifest.id for item in candidates_read)
        for candidate in sorted(candidates_read, key=lambda item: (item.manifest.id, item.root.name)):
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
            prepared = None
            source_result = None
            try:
                if type(manifest) is SourceManifest:
                    # Generator closure exits this context and cancels/disposes the job.
                    with SourcePreparation(self._executor(), self.runner_bundle, candidate.root,
                                           cache=self.cache, expected_manifest=manifest) as job:
                        with measure(timings, "source_prepare"):
                            while source_result is None:
                                source_result = job.take()
                                if source_result is None:
                                    yield
                    timings.update(source_result.timings)
                    prepared = source_result.compiled
                elif self.background:
                    self.pending = self._executor().submit(
                        _prepare, manifest, timings, self.cache
                    )
                    while not self.pending.done():
                        yield
                    future = self.pending
                    self.pending = None
                    prepared = future.result()
                elif self.cache is not None:
                    prepared = _prepare(manifest, timings, self.cache)
                if prepared is not None and prepared.cache_status is not None:
                    self._log("CACHE", id=manifest.id, **prepared.cache_status)
                if "ui-settings" in manifest.capabilities:
                    self.settings.bind(manifest.id, candidate.root)
                options = dict(report=self._diagnostic,
                               menu=self.menu.add if self.set_menu is not None else None,
                               settings=self.settings.call,
                               cleanup=lambda owner=manifest.id: self._remove_menu(owner),
                               timings=timings, prepared=prepared)
                if source_result is not None:
                    with measure(timings, "source_load"):
                        mod = SourceMod(self.runner_bundle, source_result.snapshot, **options)
                else:
                    mod = Mod(manifest, **options)
                # Retain ownership before activation so interruption can still close this instance.
                self.mods[manifest.id] = mod
                with measure(timings, "activate"):
                    mod.activate()
                if "main-menu" in manifest.capabilities:
                    with measure(timings, "registration"):
                        self._flush_menu()
            except Exception as error:
                self.failed += 1
                self.settings.remove(manifest.id)
                failed = self.mods.pop(manifest.id, None)
                if failed is not None:
                    failed.close()
                if "main-menu" in manifest.capabilities:
                    try:
                        self._flush_menu()
                    except Exception as cleanup_error:
                        self._log(
                            "CLEANUP ERROR",
                            id=manifest.id,
                            detail=str(cleanup_error)[:512],
                        )
                self._log(
                    "REJECTED",
                    id=manifest.id,
                    folder=candidate.root.name,
                    detail=str(error)[:512],
                )
            else:
                self._log("ACTIVE", id=manifest.id, version=manifest.version)
            finally:
                if source_result is not None:
                    source_result.close()
                elif prepared is not None:
                    prepared.close()  # An adopted result has already transferred its resources.
                # Emit once per attempted mod, after guest execution and cleanup have returned.
                self._log(
                    "TIMING", pack=folder.name, id=manifest.id, phase_seconds=timings
                )
            self._progress()
        self.finished = True
        self._progress("complete")
        self._log(
            "READY",
            pack=pack["id"],
            active=len(self.mods),
            phase_seconds=load_timings,
            total_seconds=round(perf_counter() - started, 6),
        )

    def _diagnostic(self, mod_id, message):
        """Route the approved report operation to the launcher log with host-owned identity."""
        self._log("REPORT", id=mod_id, message=message)

    def menu_status(self, status):
        """Revoke only UI mods if a deferred native menu installation fails."""
        # A reply queued before a later registry replacement cannot revoke the new registry.
        if status.get("generation") != self.menu_generation:
            return
        self._log("MENU", **status)
        if status.get("state") != "failed":
            return
        # The failed native registry must be cleared once, after all UI owners are revoked.
        self.applied_menu = None
        for mod_id, mod in list(self.mods.items()):
            if "main-menu" in mod.manifest.capabilities:
                self.mods.pop(mod_id).close()
                self.failed += 1
                self._log(
                    "REJECTED",
                    id=mod_id,
                    detail=status.get("detail", "Menu unavailable"),
                )
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
                    "COMPILER DRAINING",
                    detail="Guest loading cancelled; releasing compiler result when it returns",
                )
            self.pending.cancel()
            self.pending.add_done_callback(_discard)
            self.pending = None
        if self.loading is not None:
            self.loading.close()
            self.loading = None
        if self.executor is not None:
            # A native compiler cannot be interrupted by Future.cancel(); dispose its late result.
            self.executor.shutdown(wait=False, cancel_futures=True)
        failed = False
        while self.mods:
            mod_id, mod = self.mods.popitem()
            try:
                mod.close()
            except Exception as error:
                failed = True
                self._log("CLEANUP ERROR", id=mod_id, detail=str(error)[:512])
            else:
                self._log("CLOSED", id=mod_id)
        try:
            self._flush_menu()
        except Exception as error:
            failed = True
            self._log("CLEANUP ERROR", detail=str(error)[:512])
        if failed:
            raise RuntimeError("Code mod cleanup failed; see the launcher log")
