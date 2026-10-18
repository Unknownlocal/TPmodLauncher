# SPDX-License-Identifier: MPL-2.0
"""Trusted profiling metadata and bounded queries; no hooks or guest execution.

Admission supplies immutable source bytes; MetadataCache reuses their discovered
names/locations. Catalogue assigns owner-specific IDs and tracks live bindings
separately. Diagnostics consumers can only query serialized metadata pages.
"""

from collections import OrderedDict
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import sys
import time
from types import CodeType
import uuid

from hook_manager.manager import data_copy, identifier, HookError
from hook_manager.profiling_discovery import (
    code_inventory, compiled_function_id, compiled_source_identity, DiscoveryError,
)
from launcher import _settings_files as files
from mod_runtime import _source_files as disk
from mod_runtime.source import SourceSnapshot


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _load(text):
    # Reject ambiguous input instead of letting duplicate keys overwrite policy.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("Non-finite JSON number")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


class CatalogueError(ValueError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class Limits:
    source_bytes: int = 1024 * 1024
    functions: int = 4096
    sources: int = 128
    selectors: int = 16
    cursors: int = 32
    cursor_seconds: int = 30
    page_bytes: int = 3000

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("Catalogue limits must be positive integers")
        # Even an empty page or error needs room for its protocol envelope.
        if not 1024 <= self.page_bytes <= 3000:
            raise ValueError("Catalogue pages must allow bounded envelopes within 1024..3000 bytes")


@dataclass(frozen=True)
class Source:
    """An admitted module's bytes and display location, not a path to open."""
    module: str
    path: str
    content: bytes


@dataclass(frozen=True)
class CompiledSource:
    """Supply compiled Python code for function discovery when source is unavailable.

    ``module`` is the dotted module name used by catalogue selectors. ``path``
    is its relative or virtual source location shown in profiling results.
    ``code`` is a code-object root whose nested constants contain the functions
    to discover, usually the module's compiled code.

    ``Catalogue.replace`` inventories function names, locations and kinds, then
    fingerprints the code and inventory to identify changes between registrations.
    Live function binding happens separately. The caller supplies code from an
    already verified game or mod artifact.
    """
    module: str
    path: str
    code: CodeType


def _text(value):
    if type(value) is not str or not value or len(value.encode("utf-8")) > 512:
        raise ValueError("Expected nonempty metadata within 512 UTF-8 bytes")
    return value


def _owner(value):
    if type(value) is not dict or value.get("kind") not in ("game", "mod"):
        raise ValueError("Expected game or mod owner")
    if value["kind"] == "game" and set(value) == {"kind"}:
        return ("game", None)
    if value["kind"] == "mod" and set(value) == {"kind", "id"}:
        return ("mod", identifier(value["id"]))
    raise ValueError("Registration requires an exact owner")


def _discover(content, maximum):
    # Compilation creates inert code objects; never execute/import author source.
    root = compile(content, "<profiling-discovery>", "exec", dont_inherit=True, optimize=0)
    return _discover_code(root, maximum)


def _discover_code(root, maximum):
    try:
        rows, _ = code_inventory((root,), maximum_functions=maximum)
    except DiscoveryError as error:
        raise CatalogueError("resource_limit", str(error)) from error
    for name, _, discriminator, _ in rows:
        _text(name); _text(discriminator)
    return rows


class MetadataCache:
    """Data-only discovery cache in an existing host-owned directory.

    Owners, function IDs and live bindings are rebuilt, never read from this cache.
    Cache failure is a cold discovery, not a reason to prevent normal play.
    """
    MAX_BYTES = 4 * 1024 * 1024
    MAX_ENTRIES = 128
    # Compiler layout can change between Python versions. Bump discovery-2 when
    # our descriptor interpretation changes, even if the source bytes do not.
    BACKEND = f"code-discovery-2:{sys.implementation.name}:{sys.version_info.major}.{sys.version_info.minor}"

    def __init__(self, directory):
        self.directory = directory
        self.entries = OrderedDict()
        self.failure = None
        try:
            with ExitStack() as stack:
                root, parent = disk.root_directory(stack, directory)
                value = _load(files.read(root, parent, self.MAX_BYTES, filename="catalogue.json"))
            payload = value["payload"]
            # The checksum detects corruption; schema/backend checks decide
            # whether these descriptors can be reused by this implementation.
            if set(value) != {"payload", "checksum"} or value["checksum"] != _hash(payload):
                raise ValueError("Cache checksum mismatch")
            if set(payload) != {"schema", "backend", "entries"} or type(payload["schema"]) is not int or payload["schema"] != 1 or payload["backend"] != self.BACKEND:
                raise ValueError("Cache compatibility mismatch")
            if type(payload["entries"]) is not list or len(payload["entries"]) > self.MAX_ENTRIES:
                raise ValueError("Cache entry limit")
            for key, rows in payload["entries"]:
                if type(key) is not str or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                    raise ValueError("Cache key")
                if key in self.entries or type(rows) is not list or len(rows) > 4096:
                    raise ValueError("Cache rows")
                for name, line, discriminator, kind in rows:
                    _text(name); _text(discriminator)
                    if type(line) is not int or not 0 < line < 2**32 or kind not in ("function", "generator", "coroutine"):
                        raise ValueError("Cache descriptor")
                self.entries[key] = rows
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
            # Discard the whole cache if any entry is invalid. Discovery can
            # rebuild it without trusting a partially validated document.
            self.entries.clear()
            self.failure = str(error)[:256]

    def discover(self, content, maximum, *, descriptors=None):
        # The formats have separate key spaces. Compiled bytes are only used as
        # fingerprint input; cached data is never deserialized into code objects.
        key = _hash(["source" if descriptors is None else "code",
                     hashlib.sha256(content).hexdigest(), descriptors])
        if key not in self.entries:
            self.entries[key] = _discover(content, maximum) if descriptors is None else descriptors
            while len(self.entries) > self.MAX_ENTRIES:
                self.entries.popitem(last=False)
            rows = self.entries[key]
            # Bound in-memory insertions too: later owner admission may fail
            # before save() is reached.
            self._trim()
            return rows
        if len(self.entries[key]) > maximum:
            raise CatalogueError("resource_limit", "Too many cached functions")
        # A hit changes LRU ordering but not size, so it needs no reserialization.
        self.entries.move_to_end(key)
        return self.entries[key]

    def _trim(self):
        """Evict oldest descriptors until the complete persisted envelope fits."""
        while True:
            payload = {"schema": 1, "backend": self.BACKEND, "entries": list(self.entries.items())}
            data = _json({"payload": payload, "checksum": _hash(payload)}).encode("utf-8")
            if len(data) <= self.MAX_BYTES or not self.entries:
                return data
            self.entries.popitem(last=False)

    def save(self):
        try:
            data = self._trim()
            with ExitStack() as stack:
                _, parent = disk.root_directory(stack, self.directory, writable=True)
                files.write(parent, data, filename="catalogue.json")
        except (OSError, ValueError, RuntimeError) as error:
            # Persistence is optional; the admitted in-memory catalogue stays usable.
            self.failure = str(error)[:256]


class Catalogue:
    """One process generation, used on its owning controller thread.

    Only trusted admission/runtime adapters call replace/bind/unbind. Public query
    callers receive copies of metadata and cannot claim execution ownership.
    """
    def __init__(self, process, *, cache=None, limits=Limits(), clock=time.monotonic):
        self.process = identifier(process)
        self.cache, self.limits, self.clock = cache, limits, clock
        # rows: function ID -> public metadata; bindings: ID -> opaque live token.
        # Only rows are serialized. Keeping tokens separate prevents cache data
        # from claiming that code is loaded or supports a measurement.
        self.rows, self.bindings, self.cursors = {}, {}, {}
        self.generation = 0
        self.catalogue_id = uuid.uuid4().hex
        self.closed = False

    def _changed(self):
        # A query must never continue across changed metadata or readiness.
        # Check exhaustion before callers publish their replacement state.
        if self.generation >= 2**64 - 1:
            raise CatalogueError("resource_limit", "Catalogue generation exhausted")
        self.generation += 1
        self.catalogue_id = uuid.uuid4().hex
        self.cursors.clear()

    def replace(self, owner, sources, *, identity=b""):
        """Atomically replace one owner's source bundle from admitted bytes.

        identity includes loaded manifest/native artifact fingerprints when present.
        No disk scanning or normal game/module import occurs here.
        """
        if self.closed:
            raise CatalogueError("not_ready", "Catalogue is closed")
        owner_key = _owner(owner)
        if type(identity) is not bytes or len(identity) > self.limits.source_bytes:
            raise CatalogueError("resource_limit", "Identity input exceeds its byte limit")
        if type(sources) not in (list, tuple) or len(sources) > self.limits.sources:
            raise CatalogueError("resource_limit", "Source count limit")
        total, units, names = 0, [], set()
        # Validate/fingerprint the entire bundle before touching the live registry.
        for unit in sources:
            descriptors = None
            if type(unit) is Source and type(unit.content) is bytes:
                content = unit.content
            elif type(unit) is CompiledSource and type(unit.code) is CodeType:
                # Marshal is write-only here: input code came from a trusted
                # runtime provider, not a cache or a mod-supplied .pyc file.
                # Version 2 avoids reference-count-dependent flags, so loading
                # functions or retaining code references cannot change identity.
                descriptors = _discover_code(unit.code, self.limits.functions)
                content, compiled_hash = compiled_source_identity(unit.code, descriptors)
            else:
                raise ValueError("Expected admitted source bytes or a trusted code object")
            _text(unit.module); _text(unit.path)
            if not all(part.isidentifier() for part in unit.module.split(".")):
                raise ValueError("Expected a Python module name")
            path = PurePosixPath(unit.path)
            if path.is_absolute() or path.as_posix() != unit.path or any(p in ("..", ".") for p in path.parts) or ":" in unit.path or "\\" in unit.path:
                raise ValueError("Expected a relative/virtual source location")
            if unit.module in names:
                raise ValueError("Duplicate module")
            names.add(unit.module)
            total += len(content)
            if total > self.limits.source_bytes:
                raise CatalogueError("resource_limit", "Source byte limit")
            # Compiled identity already includes both bytes and inventory paths.
            source_hash = compiled_hash if type(unit) is CompiledSource else hashlib.sha256(content).hexdigest()
            units.append((unit, content, source_hash, descriptors))
        # Sort source identities so caller enumeration order cannot change IDs.
        # identity also invalidates them for manifest/native dependency changes.
        artifact = _hash([hashlib.sha256(identity).hexdigest(), sorted((u.module, u.path, h) for u, _, h, _ in units)])
        old = {key: value for key, value in self.rows.items() if _owner(value["owner"]) == owner_key}
        if old and all(row["artifact_checksum"] == artifact for row in old.values()):
            # Source equality preserves bindings and cursors; runtime replacement
            # of unchanged source must still be reported through bind/unbind.
            return tuple(sorted(old))
        replacement = {}
        # Build a candidate off to the side. Parse/limit failures leave the
        # previous admitted bundle and its live bindings intact.
        for unit, content, source_hash, descriptors in units:
            if self.cache:
                descriptors = self.cache.discover(content, self.limits.functions, descriptors=descriptors)
            elif descriptors is None:
                descriptors = _discover(content, self.limits.functions)
            for name, line, discriminator, kind in descriptors:
                function_id = compiled_function_id(owner_key, artifact, unit.module, unit.path, source_hash, discriminator)
                replacement[function_id] = {
                    "function_id": function_id, "owner": dict(owner), "module": unit.module,
                    "qualified_name": name, "artifact_checksum": artifact, "source": unit.path,
                    "line": line, "discriminator": discriminator, "kind": kind,
                    "runtime_kind": None, "binding_generation": "0", "availability": "not_loaded",
                    "reason": "no_live_binding", "metrics": [],
                }
                if len(replacement) > self.limits.functions:
                    raise CatalogueError("resource_limit", "Function limit")
        retained = {key: row for key, row in self.rows.items() if _owner(row["owner"]) != owner_key}
        if len(retained) + len(replacement) > self.limits.functions:
            raise CatalogueError("resource_limit", "Combined function limit")
        # Commit only a changed function set, after all combined limits pass.
        if set(old) != set(replacement):
            self._changed()
            retained.update(replacement)
            self.rows = retained
            # Other owners keep their bindings; this owner's new metadata starts
            # unloaded and requires fresh verification from its runtime provider.
            self.bindings = {key: value for key, value in self.bindings.items() if key in retained and key not in replacement}
        if self.cache:
            self.cache.save()
        return tuple(sorted(replacement))

    def replace_snapshot(self, snapshot):
        """Adapt the existing admitted source snapshot without rereading its files."""
        if type(snapshot) is not SourceSnapshot:
            raise ValueError("Expected an admitted source snapshot")
        manifest = snapshot.manifest
        identity = _json([manifest.id, manifest.version, manifest.api, manifest.entry, sorted(manifest.capabilities)]).encode()
        units = []
        for path, text in snapshot.files:
            module = path[:-3].replace("/", ".")
            if module.endswith(".__init__"):
                # pkg/__init__.py defines pkg, not a separate pkg.__init__ module.
                module = module[:-9]
            units.append(Source(module, "src/" + path, text.encode("utf-8")))
        return self.replace({"kind": "mod", "id": manifest.id}, units, identity=identity)

    def bind(self, function_id, binding, *, runtime_kind, metrics):
        """Record a trusted provider's live binding and verified metric support."""
        if self.closed or function_id not in self.rows:
            raise CatalogueError("unknown_target", "No registered function")
        _text(runtime_kind)
        if binding is None:
            raise ValueError("Expected a live binding token")
        if type(metrics) not in (list, tuple) or list(metrics) not in (["calls"], ["calls", "elapsed"]):
            raise ValueError("Expected supported exact metrics")
        row = self.rows[function_id]
        # Runtime-issued string tokens survive transport by value. Legacy local
        # object tokens still use identity, without calling custom equality.
        previous = self.bindings.get(function_id)
        same = previous is binding or (type(previous) is str and type(binding) is str and previous == binding)
        if same and row["runtime_kind"] == runtime_kind and row["metrics"] == list(metrics):
            # A transport retry changes neither readiness nor query cursors.
            return
        if int(row["binding_generation"]) >= 2**64 - 1:
            raise CatalogueError("resource_limit", "Binding generation exhausted")
        self._changed()
        self.bindings[function_id] = binding
        row.update(runtime_kind=runtime_kind, metrics=list(metrics), availability="ready", reason=None,
                   binding_generation=str(int(row["binding_generation"]) + 1))

    def unbind(self, function_id, *, availability="not_loaded", reason="binding_removed"):
        """Keep source discoverable while removing verified measurement support."""
        if self.closed or function_id not in self.rows:
            raise CatalogueError("unknown_target", "No registered function")
        if availability not in ("not_loaded", "unsupported", "unknown"):
            raise ValueError("Expected unavailable state")
        identifier(reason)
        row = self.rows[function_id]
        if int(row["binding_generation"]) >= 2**64 - 1:
            raise CatalogueError("resource_limit", "Binding generation exhausted")
        self._changed()
        self.bindings.pop(function_id, None)
        row.update(availability=availability, reason=reason, metrics=[],
                   binding_generation=str(int(row["binding_generation"]) + 1))

    def _select(self, selectors):
        if type(selectors) is not list or not 0 < len(selectors) <= self.limits.selectors:
            raise CatalogueError("resource_limit", "Selector count limit")
        selected = set()
        for selector in selectors:
            if type(selector) is not dict or not {"owner"} <= set(selector) <= {"owner", "module", "qualified_name", "function_id"}:
                raise ValueError("Invalid selector fields")
            owner = selector["owner"]
            if owner == {"kind": "mod"}:
                # Queries may span mods; registration always requires an exact owner.
                owner_key = ("mod", None)
            else:
                owner_key = _owner(owner)
            for field in set(selector) - {"owner"}:
                _text(selector[field])
            if "function_id" in selector and len(selector) != 2:
                raise ValueError("Function IDs cannot be combined with module/name")
            for key, row in self.rows.items():
                row_owner = _owner(row["owner"])
                if row_owner[0] != owner_key[0] or (owner_key[1] is not None and owner_key[1] != row_owner[1]):
                    continue
                # Fields within one selector intersect; multiple selectors form
                # a deduplicated union. Sorting makes page order deterministic.
                if all(row[field] == value for field, value in selector.items() if field != "owner"):
                    selected.add(key)
        return tuple(sorted(selected))

    def resolve(self, selectors):
        """Copy a trusted admission selection, including opaque runtime tokens.

        This is internal plan input, not the public paged query. Tokens must be
        plain strings issued by the execution runtime; Python functions/code do
        not cross the process boundary. Unsupported legacy object tokens remain
        discoverable but cannot be admitted through this data-only path.
        """
        if self.closed:
            raise CatalogueError("not_ready", "Catalogue is closed")
        selected = self._select(selectors)
        if len(selected) > 256:
            # Match the native target quota before producing a candidate plan;
            # a broad selector must not become an arbitrary truncated capture.
            raise CatalogueError("resource_limit", "Capture function limit")
        functions = []
        for key in selected:
            record = data_copy(self.rows[key])
            token = self.bindings.get(key)
            # Do not serialize legacy Python objects. None leaves metadata
            # discoverable while runtime admission reports the binding unusable.
            # Token shape alone grants no authority; the runtime checks identity.
            record["binding_token"] = token if type(token) is str and len(token) == 32 else None
            functions.append(record)
        # Retain unmatched selectors separately: zero matches is missing coverage,
        # whereas a valid selected function may genuinely execute zero times.
        return {"process": self.process, "functions": functions,
                "unmatched": [data_copy(selector) for selector in selectors if not self._select([selector])]}

    def apply_bindings(self, report):
        """Apply a trusted runtime readiness report with stale-generation checks.

        This is the host half of provider admission, not a mod-callable operation.
        Validate the entire bounded batch before changing rows/cursors. Retries
        already describing current state are no-ops, including transported tokens.
        """
        if self.closed:
            raise CatalogueError("not_ready", "Catalogue is closed")
        if (type(report) is not dict or set(report) != {"process", "runtime", "bindings"}
                or report["process"] != self.process):
            raise CatalogueError("stale_catalogue", "Provider report belongs to another process")
        identifier(report["runtime"])
        if type(report["bindings"]) is not list or not 1 <= len(report["bindings"]) <= 256:
            raise CatalogueError("resource_limit", "Provider update count limit")
        changes, seen = [], set()
        fields = {"function_id", "artifact_checksum", "expected_generation", "binding_token",
                  "runtime_kind", "metrics", "availability", "reason"}
        for value in report["bindings"]:
            update = data_copy(value)
            if type(update) is not dict or set(update) != fields:
                raise ValueError("Invalid provider update fields")
            key = update["function_id"]
            if type(key) is not str or key in seen or key not in self.rows:
                raise CatalogueError("stale_catalogue", "Unknown or duplicate provider function")
            seen.add(key)
            row = self.rows[key]
            if update["artifact_checksum"] != row["artifact_checksum"]:
                raise CatalogueError("stale_catalogue", "Provider artifact changed")
            generation = update["expected_generation"]
            if (type(generation) is not str or not generation.isascii() or not generation.isdecimal()
                    or str(int(generation)) != generation or not 0 <= int(generation) < 2**64):
                raise ValueError("Expected canonical provider generation")
            availability, token = update["availability"], update["binding_token"]
            _text(update["runtime_kind"])
            if availability == "ready":
                if (type(token) is not str or len(token) != 32 or any(c not in "0123456789abcdef" for c in token)
                        or update["metrics"] != ["calls", "elapsed"] or update["reason"] is not None):
                    raise ValueError("Expected verified synchronous provider binding")
                previous = self.bindings.get(key)
                same = (row["availability"] == "ready" and type(previous) is str and previous == token
                        and row["runtime_kind"] == update["runtime_kind"] and row["metrics"] == update["metrics"])
            else:
                if availability not in ("not_loaded", "unsupported", "unknown") or token is not None or update["metrics"] != []:
                    raise ValueError("Expected unavailable provider binding")
                identifier(update["reason"])
                same = row["availability"] == availability and row["reason"] == update["reason"] and key not in self.bindings
            # An exact state retry may carry the generation from before its first
            # application. A different state requires the current generation,
            # preventing a delayed report from replacing a newer live binding.
            if not same:
                if generation != row["binding_generation"]:
                    raise CatalogueError("stale_catalogue", "Provider binding generation changed")
                if int(row["binding_generation"]) >= 2**64 - 1:
                    raise CatalogueError("resource_limit", "Binding generation exhausted")
                changes.append(update)
        if self.generation + len(changes) >= 2**64:
            raise CatalogueError("resource_limit", "Catalogue generation exhausted")
        for update in changes:
            key = update["function_id"]
            if update["availability"] == "ready":
                self.bind(key, update["binding_token"], runtime_kind=update["runtime_kind"], metrics=update["metrics"])
            else:
                self.unbind(key, availability=update["availability"], reason=update["reason"])
        return {key: self.rows[key]["binding_generation"] for key in sorted(seen)}

    def query(self, requester, text):
        """Return one bounded JSON page; continuation tokens are single use."""
        try:
            if self.closed:
                raise CatalogueError("not_ready", "Catalogue is closed")
            requester = identifier(requester)
            if type(text) is not str or len(text) > 3000 or len(text.encode("utf-8")) > 3000:
                raise ValueError("Request byte limit")
            request = data_copy(_load(text))
            if type(request) is not dict or type(request.get("schema")) is not int or request["schema"] != 1:
                raise CatalogueError("unsupported_schema", "Expected schema 1")
            now = self.clock()
            self.cursors = {key: value for key, value in self.cursors.items() if value[1] > now}
            token = request.get("cursor")
            if token is not None:
                if set(request) != {"schema", "cursor"} or type(token) is not str:
                    raise ValueError("Invalid continuation")
                state = self.cursors.get(token)
                if state is None or state[0] != requester:
                    raise CatalogueError("stale_catalogue", "Unknown, expired or foreign cursor")
                _, expiry, selected, offset = state
                # Continuation keeps the original deadline; repeated reads must
                # not keep a bounded cursor reservation alive indefinitely.
            else:
                if not {"schema", "selectors"} <= set(request) <= {"schema", "selectors", "cursor"}:
                    raise ValueError("Invalid initial query")
                selected, offset, expiry = self._select(request["selectors"]), 0, now + self.limits.cursor_seconds
            # Reserve the next token's bytes while testing page size, even if the
            # last page will ultimately replace it with null.
            next_token = uuid.uuid4().hex
            data = {"catalogue_id": self.catalogue_id, "generation": str(self.generation),
                    "functions": [], "next_cursor": next_token}
            result = {"schema": 1, "ok": True, "data": data}
            index = offset
            while index < len(selected):
                # Trial append checks both value-tree and UTF-8 envelope limits;
                # remove the candidate whole rather than truncate its identity.
                data["functions"].append(self.rows[selected[index]])
                try:
                    data_copy(result)
                    fits = len(_json(result).encode("utf-8")) <= self.limits.page_bytes
                except HookError:
                    fits = False
                if not fits:
                    data["functions"].pop()
                    break
                index += 1
            if index == offset and index < len(selected):
                raise CatalogueError("record_too_large", "Function record cannot fit a page")
            if index == len(selected):
                data["next_cursor"] = None
            else:
                # A successful continuation replaces one cursor reservation;
                # it does not need an extra slot when the cursor table is full.
                if len(self.cursors) - (token is not None) >= self.limits.cursors:
                    raise CatalogueError("resource_limit", "Cursor limit")
                # Token state: owner, fixed deadline, frozen matched IDs, next offset.
                # Catalogue mutation clears all tokens, so these IDs stay valid.
                self.cursors[next_token] = (requester, expiry, selected, index)
            if token is not None:
                # Consume only after the response fits and any successor is stored.
                # Invalid or foreign requests never reach this removal.
                self.cursors.pop(token)
            return _json(result)
        except (ValueError, HookError, TypeError, RecursionError, UnicodeError) as error:
            code = error.code if isinstance(error, CatalogueError) else "invalid_request"
            return _json({"schema": 1, "ok": False, "error": {"code": code, "detail": str(error)[:128]}})

    def release(self, requester):
        self.cursors = {key: value for key, value in self.cursors.items() if value[0] != requester}

    def close(self):
        self.rows.clear()
        self.bindings.clear()
        self.cursors.clear()
        self.closed = True
