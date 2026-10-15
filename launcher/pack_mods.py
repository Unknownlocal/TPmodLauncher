# SPDX-License-Identifier: MPL-2.0
"""Discover and own sandboxed code mods for one process's selected Formula pack."""

from collections import Counter
import json
import os
from pathlib import Path

from launcher.bundled_dependencies import local_path, safe_name
from launcher.mod_contract import Manifest, ManifestError, Mod
from hook_manager.menu import MenuRequests

# Bound discovery and the number of components admitted by one game process.
MAX_PACK_ENTRIES = 4096
MAX_MODS = 32


class PackMods:
    """Load once on the controller thread; close all instances with their game session."""

    def __init__(self, game_root, report, *, set_menu=None):
        # game_root and report come from the launcher, never from a mod manifest.
        self.game_root = Path(game_root).resolve(strict=True)
        self.report = report
        self.mods = {}
        self.started = False
        self.closed = False
        self.menu = MenuRequests()
        self.set_menu = set_menu
        self.applied_menu = []
        self.menu_generation = 0

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

    def load(self, pack):
        """Admit and activate this snapshot once; one failed mod does not stop its neighbours."""
        if self.started or self.closed:
            return
        # Repeated or changed bridge records cannot reassign an existing process's mods.
        self.started = True
        try:
            folder = self._folder(pack)
            candidates = self._candidates(folder)
        except (OSError, ValueError, RuntimeError) as error:
            self._log("DISCOVERY REJECTED", detail=str(error)[:512])
            return
        manifests = []
        for path in candidates:
            try:
                manifest = Manifest.read(path)
                # Recheck after resolution in case a parent changed during discovery.
                if manifest.root.parent != folder:
                    raise ManifestError(
                        "Code mod is not a direct child of the selected pack"
                    )
                manifests.append(manifest)
            except ManifestError as error:
                self._log("REJECTED", folder=path.name, detail=str(error)[:512])
        counts = Counter(manifest.id for manifest in manifests)
        for manifest in sorted(manifests, key=lambda item: (item.id, item.root.name)):
            # Decide duplicate identity before initializing either copy; never pick a winner.
            if counts[manifest.id] > 1:
                self._log(
                    "REJECTED",
                    id=manifest.id,
                    folder=manifest.root.name,
                    detail="Duplicate mod ID",
                )
                continue
            try:
                mod = Mod(
                    manifest,
                    report=self._diagnostic,
                    menu=self.menu.add if self.set_menu is not None else None,
                    cleanup=lambda owner=manifest.id: self._remove_menu(owner),
                )
                # Retain ownership before activation so interruption can still close this instance.
                self.mods[manifest.id] = mod
                mod.activate()
                if "main-menu" in manifest.capabilities:
                    self._flush_menu()
            except Exception as error:
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
                    folder=manifest.root.name,
                    detail=str(error)[:512],
                )
            else:
                self._log("ACTIVE", id=manifest.id, version=manifest.version)
        self._log("READY", pack=pack["id"], active=len(self.mods))

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
                self._log(
                    "REJECTED",
                    id=mod_id,
                    detail=status.get("detail", "Menu unavailable"),
                )
        self._flush_menu()

    def close(self):
        """Close in reverse activation order, attempting every instance even if cleanup fails."""
        if self.closed:
            return
        self.closed = True
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
