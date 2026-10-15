# SPDX-License-Identifier: MPL-2.0
"""Read one explicit mod manifest and enforce the experimental 0.1.0 Component API."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import tomllib

from hook_manager.manager import data_copy, identifier
from launcher.bundled_dependencies import local_path
from launcher.sandbox import Limits, Operation, Sandbox, SandboxError

API_VERSION = "0.1.0"
EXPORTS = {"activate": ((), None), "on-event": (("string", "string"), "string")}
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
    runtime: str
    component: str
    capabilities: frozenset[str]

    @classmethod
    def read(cls, folder):
        """Read mod.toml in this explicit folder; discovery of folders belongs to the next step."""
        try:
            root = Path(folder)
            if root.is_symlink() or root.is_junction():
                raise ValueError("Mod folder must not be linked")
            root = root.resolve(strict=True)
            # Bound the read itself, rather than loading an arbitrarily large manifest first.
            with local_path(root, "mod.toml").open("rb") as source:
                content = source.read(16_384 + 1)
            if len(content) > 16_384:
                raise ValueError("Manifest exceeds 16 KiB")
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
            if data["api"] != API_VERSION or data["runtime"] not in ("wasm", "python"):
                raise ValueError("Unsupported API version or runtime profile")
            component = data["component"]
            path = local_path(root, component)
            if path.relative_to(root).as_posix() != component or path.suffix != ".wasm":
                raise ValueError("Expected a canonical relative .wasm component path")
            capabilities = data["capabilities"]
            if (
                type(capabilities) is not list
                or any(
                    type(name) is not str or name != "diagnostics"
                    for name in capabilities
                )
                or len(capabilities) != len(set(capabilities))
            ):
                raise ValueError("Unknown or duplicate capability")
            return cls(
                root,
                mod_id,
                data["version"],
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
            with local_path(self.root, self.component).open("rb") as source:
                component = source.read(Limits().component_bytes + 1)
            if len(component) > Limits().component_bytes:
                raise ValueError("Component exceeds 32 MiB")
            return component
        except (OSError, ValueError, RuntimeError) as error:
            raise ManifestError(str(error)[:512]) from None


class Mod:
    """Own one admitted contract instance; trusted code decides which events it receives."""

    def __init__(self, manifest: Manifest, *, report=None, cleanup=None):
        # A manifest requests permissions; only this fixed registry can turn them into operations.
        try:
            operations = {}
            if "diagnostics" in manifest.capabilities:
                if not callable(report):
                    raise ManifestError("Diagnostics requires a trusted report handler")

                def emit(message):
                    # Keep mod text within one printable diagnostic record; identity comes from the host.
                    report(
                        manifest.id,
                        "".join(
                            char if char.isprintable() else " " for char in message
                        ),
                    )

                operations["report"] = Operation(("string",), None, emit)
            component = manifest.read_component()
        except BaseException:
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
        )
        # Admission and activation are distinct; construction cannot silently invoke a lifecycle export.
        self._active = False

    def activate(self):
        """Invoke activation once; repeated activation is a caller error."""
        if self._active:
            raise SandboxError("Mod has already been activated")
        self.sandbox.call("activate")
        self._active = True

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
            result = self.sandbox.call("on-event", event, payload)
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
