# SPDX-License-Identifier: MPL-2.0
"""Validate mod metadata and enforce the supported versioned Component APIs."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tomllib

from hook_manager.manager import data_copy, identifier
from launcher.bundled_dependencies import local_path
from launcher.load_timing import measure
from launcher.mod_api import CAPABILITIES, EXPORTS
from launcher.sandbox import Limits, Operation, Sandbox, SandboxError

# Fuel counts Wasm work, including the guest Python interpreter (see Limits).
# Measured startup needs about 12 million units and more than 32 core instances.
# Startup gets five seconds to allow slower hosts; activate/on-event retain one second.
# Only initialization gets extra fuel; activate/on-event each retain 5 million units.
PYTHON_LIMITS = Limits(
    initialization_fuel=20_000_000, initialization_seconds=5.0, instances=64
)
_FIELDS = {"schema", "id", "version", "api", "runtime", "component", "capabilities"}
# SemVer numeric identifiers have no leading zero; prereleases may also contain words.
_NUMBER = r"(?:0|[1-9][0-9]*)"
_PRE = rf"(?:{_NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}(?:-{_PRE}(?:\.{_PRE})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"


class ManifestError(ValueError):
    """A manifest or referenced component is invalid or outside its own mod folder."""


@dataclass(frozen=True)
class Manifest:
    """Validated metadata; reading it never executes source or loads a component."""

    root: Path
    id: str
    version: str
    api: str
    runtime: str
    component: str
    capabilities: frozenset[str]

    @classmethod
    def read(cls, folder):
        """Read mod.toml in this explicit folder without importing any mod code."""
        try:
            root = Path(folder)
            if root.is_symlink() or root.is_junction():
                raise ValueError("Mod folder must not be linked")
            root = root.resolve(strict=True)
            # Bound the read itself, rather than loading an arbitrarily large manifest first.
            path = local_path(root, "mod.toml")
            # FIFOs and devices can block an unbounded read; accept ordinary files only.
            if not path.is_file():
                raise ValueError("Manifest must be a regular file")
            with path.open("rb") as source:
                content = source.read(16_384 + 1)
            if len(content) > 16_384:
                raise ValueError("Manifest exceeds 16 KiB")
            return cls.parse(root, content)
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            raise ManifestError(str(error)[:512]) from None

    @classmethod
    def parse(cls, root, content):
        """Validate bounded metadata already read by trusted pack admission."""
        try:
            if type(content) is not bytes or len(content) > 16_384:
                raise ValueError("Expected at most 16 KiB of manifest bytes")
            data = tomllib.loads(content.decode("utf-8"))
            if (
                set(data) != _FIELDS
                or type(data["schema"]) is not int
                or data["schema"] != 1
            ):
                raise ValueError("Expected manifest schema 1 and its documented fields")
            mod_id = identifier(data["id"])
            if (
                type(data["version"]) is not str
                or len(data["version"]) > 64
                or not re.fullmatch(_VERSION, data["version"])
            ):
                raise ValueError("Expected a short SemVer version")
            if (
                type(data["api"]) is not str
                or data["api"] not in CAPABILITIES
                or data["runtime"] not in ("wasm", "python")
            ):
                raise ValueError("Unsupported API version or runtime profile")
            component = data["component"]
            path = local_path(root, component)
            if path.relative_to(root).as_posix() != component or path.suffix != ".wasm":
                raise ValueError("Expected a canonical relative .wasm component path")
            capabilities = data["capabilities"]
            if (
                type(capabilities) is not list
                or any(
                    type(name) is not str or name not in CAPABILITIES[data["api"]]
                    for name in capabilities
                )
                or len(capabilities) != len(set(capabilities))
            ):
                raise ValueError("Unknown or duplicate capability")
            return cls(
                root,
                mod_id,
                data["version"],
                data["api"],
                data["runtime"],
                component,
                frozenset(capabilities),
            )
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            raise ManifestError(str(error)[:512]) from None

    def read_component(self):
        """Read bounded portable bytes, rechecking the file path before opening it."""
        try:
            # Do not let a folder replaced since manifest validation redirect this later read.
            if (
                self.root.is_symlink()
                or self.root.is_junction()
                or self.root.resolve(strict=True) != self.root
            ):
                raise ValueError("Mod folder changed or became linked")
            path = local_path(self.root, self.component)
            if not path.is_file():
                raise ValueError("Component must be a regular file")
            with path.open("rb") as source:
                component = source.read(Limits().component_bytes + 1)
            if len(component) > Limits().component_bytes:
                raise ValueError("Component exceeds 32 MiB")
            return component
        except (OSError, ValueError, RuntimeError) as error:
            raise ManifestError(str(error)[:512]) from None


def _host_operations(manifest, report, menu, settings=None, theme=None):
    """Attach host-owned identity to the launcher's approved mod operations."""
    operations = {}
    if "diagnostics" in manifest.capabilities:
        if not callable(report):
            raise ManifestError("Diagnostics requires a trusted report handler")

        def emit(message):
            report(
                manifest.id,
                "".join(char if char.isprintable() else " " for char in message),
            )

        operations["report"] = Operation(("string",), None, emit)
    if "main-menu" in manifest.capabilities:
        if not callable(menu):
            raise ManifestError("Main menu requires a trusted menu provider")
        operations["add-menu-button"] = Operation(
            ("string", "string", "string"),
            None,
            lambda item_id, label, message: menu(manifest.id, item_id, label, message),
        )
    if "ui-settings" in manifest.capabilities:
        if not callable(settings):
            raise ManifestError("UI settings requires a trusted settings provider")
        if not callable(theme):
            raise ManifestError("UI settings requires a trusted theme provider")
        for name in ("register-settings", "save-settings"):
            operations[name] = Operation(
                ("string",), "string",
                lambda text, name=name: settings(manifest.id, name, text),
            )
        operations["register-theme"] = Operation(("string",), None, lambda text: theme(manifest.id, text))
    return operations


class Mod:
    """Own one admitted contract instance; trusted code decides which events it receives."""

    def __init__(
        self,
        manifest: Manifest,
        *,
        report=None,
        menu=None,
        settings=None,
        theme=None,
        cleanup=None,
        timings=None,
        prepared=None,
    ):
        # A manifest requests permissions; only this fixed registry can turn them into operations.
        try:
            operations = _host_operations(manifest, report, menu, settings, theme)
            # The loader owns this timing dictionary; it is never passed into guest code.
            if prepared is None:
                with measure(timings, "component_read"):
                    component = manifest.read_component()
            else:
                component = prepared.data
        except BaseException:
            if prepared is not None:
                prepared.close()
            # Own admission cleanup until Sandbox adopts it, including missing component/provider errors.
            if cleanup is not None:
                cleanup()
            raise
        self.manifest = manifest
        # Sandbox owns cleanup from this point, even if its constructor raises.
        self.sandbox = Sandbox(
            component,
            operations,
            limits=PYTHON_LIMITS if manifest.runtime == "python" else Limits(),
            cleanup=cleanup,
            required_exports=EXPORTS,
            timings=timings,
            prepared=prepared,
        )
        # Admission and activation are distinct; construction cannot silently invoke a lifecycle export.
        self._active = False

    def activate(self):
        """Invoke activation once; repeated activation is a caller error."""
        if self._active:
            raise SandboxError("Mod has already been activated")
        self._activate()
        self._active = True

    def _activate(self):
        self.sandbox.call("activate")

    def _on_event(self, name, payload):
        return self.sandbox.call("on-event", name, payload)

    def event(self, name, value):
        """Send/receive bounded JSON plain values through the typed on-event export."""
        if not self._active:
            raise SandboxError("Activate the mod before sending events")
        try:
            event = identifier(name)
            payload = json.dumps(
                data_copy(value),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            result = self._on_event(event, payload)
            # Reuse the manager's existing value limits; JSON strings never become executable commands.
            return data_copy(json.loads(result))
        except Exception as error:
            self.sandbox.failure = self.sandbox.failure or str(error)[:512]
            self.close()
            raise SandboxError(self.sandbox.failure) from None

    def close(self):
        """Release this mod's grants and owned cleanup scope."""
        self.sandbox.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
