# SPDX-License-Identifier: MPL-2.0
"""Reuse adjacent Workshop file checks without caching metadata across steps."""

from functools import update_wrapper
import hashlib
import json
import stat
import sys
from types import CodeType

if __package__:
    from .diagnostics import build_log_record
    from .manager import HookError, Manager, Target


class WorkshopMetadata(Manager):
    """Optimise only the inspected validator and restore it on bridge detach.

    The walk, native constants and ancestor checks remain game-owned. Unknown
    implementations retain native behaviour rather than borrowing these rules.
    """

    # Fingerprints include nested code, constants and exception tables, excluding
    # source locations. A game update changing validation must be reviewed first.
    CODE_DIGESTS = {
        "_content_files": "8322c4f04b273ba59741bef6a016a71ac399c45b1d99b5da3b0c628e14845af5",
        "_is_link": "941e3d32e5b2f1b02d514be7494b81594c1f3576d7bb881d26701595906189f2",
    }

    def __init__(self, activity):
        self.activity = activity
        super().__init__(lambda record: None)
        target = Target("workshop.metadata", "workshop_manager", (), "_content_files",
                        "(root: 'Path', *, packaging=False, content_type='season') -> 'list[Path]'")
        self.approve(target)
        try:
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
        except HookError:
            pass  # Manager retains the compatibility warning; keep native validation.

    @staticmethod
    def code_digest(code):
        """Identify validation behaviour independently of archive source paths."""
        def describe(value):
            if isinstance(value, CodeType):
                return (value.co_code.hex(), describe(value.co_consts), value.co_names,
                        value.co_varnames, value.co_freevars, value.co_cellvars,
                        value.co_argcount, value.co_posonlyargcount, value.co_kwonlyargcount,
                        value.co_flags, value.co_exceptiontable.hex())
            if isinstance(value, tuple):
                return [describe(item) for item in value]
            if isinstance(value, frozenset):
                return {"frozenset": sorted(value)}
            return value
        return hashlib.sha256(json.dumps(describe(code), separators=(",", ":")).encode()).hexdigest()

    def store_error(self, error):
        """Retain and submit a warning; unavailable optimisation is not fatal."""
        self.errors.append(str(error)[:4096])
        del self.errors[:-16]
        self.error_record = build_log_record("WARNING", "Workshop metadata optimisation unavailable: " + str(error),
                                             component="workshop")
        try:
            # The writer formats traceback details; imports must not read source
            # files. Reporting failure must also leave native validation usable.
            self.activity.submit_log({**self.error_record, "event": "diagnostic"}, error=error)
        except Exception:
            pass  # The compatibility failure is still retained in status.

    def _wrapper(self, name, binding, target, original, signature):
        native = sys.modules[target.module]
        for attribute, expected in self.CODE_DIGESTS.items():
            function = getattr(native, attribute)
            if (hasattr(function, "__wrapped__") or not hasattr(function, "__code__")
                    or self.code_digest(function.__code__) != expected):
                raise HookError("Workshop validation implementation changed: " + attribute)
        if original.__globals__ is not vars(native):
            raise HookError("Workshop validator uses an unexpected namespace")
        link_check = native._is_link

        def content_files(root, *, packaging=False, content_type="season"):
            # Escaped wrappers after detach or a changed native link policy use
            # the original. An admitted walk may finish normally during detach.
            if not self.group_intact(name) or native._is_link is not link_check or native.stat is not stat:
                return original(root, packaging=packaging, content_type=content_type)
            return self.content_files(native, root, packaging=packaging, content_type=content_type)

        try:
            self.activity.submit_log({**build_log_record("DEBUG", "Workshop metadata optimisation prepared: "
                "two fresh status checks per accepted file", component="workshop"), "event": "diagnostic"})
        except Exception:
            pass  # A diagnostic outbox failure must not change validation.
        return update_wrapper(content_files, original)

    @staticmethod
    def content_files(native, root, *, packaging=False, content_type="season"):
        """Keep the inspected native rules, sharing only adjacent status decisions.

        Metadata describes a path at a point in time, not a pinned file handle.
        Retain a fresh size-check boundary after filename validation and reject
        observed replacement. Hashing/copying retain their existing later reads;
        this does not claim an atomic snapshot of the whole directory tree.
        """
        native._reject_links_to(root)
        if not root.is_dir():
            raise native.WorkshopValidationError("The pack directory does not exist.")
        files, total = [], 0
        ignored_dirs = native.IGNORED_DIRS if content_type == "season" else {".git", ".svn", "__pycache__"}
        for directory, dirs, names in native.os.walk(root, followlinks=False):
            parent = native.Path(directory)
            for dirname in list(dirs):
                candidate = parent / dirname
                if native._is_link(candidate):
                    raise native.WorkshopValidationError(f"Links and junctions are not allowed: {candidate.relative_to(root)}")
                if dirname.lower() in ignored_dirs:
                    if packaging:
                        dirs.remove(dirname)
                    else:
                        raise native.WorkshopValidationError(f"Runtime directories are not allowed: {dirname}")
            for name in sorted(names):
                path = parent / name
                relative = path.relative_to(root)
                info = path.lstat()
                # One fresh non-follow result serves both link/reparse and type
                # checks. Never let a later following stat read a link target.
                if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                    raise native.WorkshopValidationError(f"Only regular files are allowed: {relative}")
                if any(part.endswith((".", " ")) or ":" in part or "\0" in part for part in relative.parts):
                    raise native.WorkshopValidationError(f"Unsupported file name: {relative}")
                extension = path.suffix.lower()
                if packaging and name.lower() in native.IGNORED_FILES:
                    continue
                if content_type == "other":
                    if len(relative.parts) == 1 and name.casefold() == native.MANIFEST_NAME and name != native.MANIFEST_NAME:
                        raise native.WorkshopValidationError("Use lowercase workshop.json for the reserved Workshop manifest.")
                    if extension not in native.OTHER_DATA_EXTENSIONS:
                        raise native.WorkshopValidationError(
                            f"Other packs accept JSON, PNG, WAV, OGG, MP3, FLAC, MP4 and WEBM files (no Python): {relative}")
                elif relative.as_posix() != "constants.py" and extension not in native.DATA_EXTENSIONS:
                    if packaging and extension in {".pyc", ".pyo"}:
                        continue
                    raise native.WorkshopValidationError(f"Unsupported or executable file: {relative}")
                current = path.lstat()
                if (not stat.S_ISREG(current.st_mode) or getattr(current, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                        or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
                    raise native.WorkshopValidationError(f"File changed during validation: {relative}")
                size = current.st_size
                total += size
                if size > native.MAX_FILE_BYTES or total > native.MAX_TOTAL_BYTES or len(files) >= native.MAX_FILES:
                    raise native.WorkshopValidationError("The pack exceeds supported file/size limits.")
                files.append(path)
        names = [path.relative_to(root).as_posix().casefold() for path in files]
        if len(names) != len(set(names)):
            raise native.WorkshopValidationError("The pack contains names that collide on Windows.")
        if content_type == "other":
            components = {}
            for path in files:
                relative = path.relative_to(root)
                for index, part in enumerate(relative.parts, 1):
                    spelling = "/".join(relative.parts[:index])
                    key = spelling.casefold()
                    if key in components and components[key] != spelling:
                        raise native.WorkshopValidationError("The pack contains folder names that collide on Windows.")
                    components[key] = spelling
                    if (any(c in part for c in '<>"|?*') or any(ord(c) < 32 for c in part)
                            or part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL",
                                *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}):
                        raise native.WorkshopValidationError(f"Unsupported Windows file name: {relative}")
        return sorted(files, key=lambda path: path.relative_to(root).as_posix())
