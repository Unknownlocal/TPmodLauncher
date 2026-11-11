# SPDX-License-Identifier: MPL-2.0
"""Validate real disposable pack files, mutation boundaries and adapter ownership."""

import os
from pathlib import Path
import stat
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.workshop_metadata import WorkshopMetadata


@pytest.fixture
def native(monkeypatch):
    module = ModuleType("workshop_manager")
    module.__dict__.update(Path=Path, stat=stat, os=os, WorkshopValidationError=ValueError,
        IGNORED_DIRS={"cache", "logs", "saves", ".svn", "__pycache__", ".git"},
        IGNORED_FILES={"desktop.ini", ".ds_store", "thumbs.db"}, MANIFEST_NAME="workshop.json",
        DATA_EXTENSIONS={".json", ".png", ".txt"}, OTHER_DATA_EXTENSIONS={".json", ".png"},
        MAX_FILE_BYTES=1024, MAX_TOTAL_BYTES=2048, MAX_FILES=10)
    exec('''
def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)

def _reject_links_to(path):
    for component in (path.absolute(), *path.absolute().parents):
        if component.exists() and _is_link(component):
            raise WorkshopValidationError('Linked ancestor')

def _content_files(root: 'Path', *, packaging=False, content_type='season') -> 'list[Path]':
    return ['native validation']
''', vars(module))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


@pytest.fixture
def installed(native, monkeypatch):
    # The portable fixture is not game bytecode. Only its admission fingerprints
    # differ; the real manager, replacement, file checks and restoration execute.
    monkeypatch.setattr(WorkshopMetadata, "CODE_DIGESTS", {
        name: WorkshopMetadata.code_digest(getattr(native, name).__code__)
        for name in WorkshopMetadata.CODE_DIGESTS})
    records = []
    adapter = WorkshopMetadata(SimpleNamespace(submit_log=lambda record, **kw: records.append(record)))
    try:
        assert adapter.status()["groups"] == {"workshop.metadata": "active"}, records
        yield adapter
    finally:
        adapter.stop()


def test_real_pack_uses_two_checks_per_file_and_new_calls_see_new_sizes(native, installed, tmp_path, monkeypatch):
    files = [tmp_path / "constants.py", tmp_path / "images/z.png", tmp_path / "teams.json"]
    files[1].parent.mkdir()
    for file in files:
        file.write_bytes(b"data")
    counts = {path: 0 for path in files}
    original = Path.lstat
    def checked(path, *args, **kwargs):
        if path in counts:
            counts[path] += 1
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", checked)
    result = native._content_files(tmp_path)
    assert result == sorted(files, key=lambda path: path.relative_to(tmp_path).as_posix())
    assert set(counts.values()) == {2}
    files[-1].write_bytes(b"x" * 1025)
    with pytest.raises(ValueError, match="file/size limits"):
        native._content_files(tmp_path)


def test_packaging_keeps_native_exclusions_and_other_pack_rules(native, installed, tmp_path):
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache/hidden.json").write_text("{}")
    for name in ("teams.json", "desktop.ini", "compiled.pyc"):
        (tmp_path / name).write_text("{}")
    with pytest.raises(ValueError, match="Runtime directories"):
        native._content_files(tmp_path)
    assert native._content_files(tmp_path, packaging=True) == [tmp_path / "teams.json"]
    with pytest.raises(ValueError, match="no Python"):
        native._content_files(tmp_path, packaging=True, content_type="other")


@pytest.mark.parametrize("limit,value", [("MAX_FILE_BYTES", 3), ("MAX_TOTAL_BYTES", 7), ("MAX_FILES", 1)])
def test_native_limits_remain_enforced(native, installed, tmp_path, limit, value):
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_bytes(b"data")
    setattr(native, limit, value)
    with pytest.raises(ValueError, match="file/size limits"):
        native._content_files(tmp_path)


@pytest.mark.parametrize("change", ["replace", "grow", "remove", "metadata_error", "reparse"])
def test_changes_at_fresh_size_boundary_are_rejected(native, installed, tmp_path, monkeypatch, change):
    target = tmp_path / "teams.json"
    target.write_bytes(b"data")
    original, calls = Path.lstat, 0
    error = PermissionError("Fixture metadata denied")
    def changing(path, *args, **kwargs):
        nonlocal calls
        if path != target:
            return original(path, *args, **kwargs)
        calls += 1
        if calls == 2:
            if change == "replace":
                target.replace(tmp_path.parent / (tmp_path.name + "-old.json"))
                target.write_bytes(b"new")
            elif change == "grow":
                target.write_bytes(b"x" * 1025)
            elif change == "remove":
                target.unlink()
            elif change == "metadata_error":
                raise error
        info = original(path, *args, **kwargs)
        if change == "reparse" and calls == 2:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, "lstat", changing)
    expected = PermissionError if change == "metadata_error" else FileNotFoundError if change == "remove" else ValueError
    with pytest.raises(expected) as failure:
        native._content_files(tmp_path)
    if change == "metadata_error":
        assert failure.value is error
    assert calls == 2


def test_links_are_rejected_before_following_the_target(native, installed, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_text("{}")
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable on this host")
    with pytest.raises(ValueError, match="Only regular files"):
        native._content_files(tmp_path)
    linked.unlink()
    linked.symlink_to(outside.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="Links and junctions"):
        native._content_files(tmp_path)


def test_nonregular_candidates_and_initial_metadata_errors_keep_their_outcome(native, installed, tmp_path, monkeypatch):
    candidate = tmp_path / "teams.json"
    candidate.mkdir()
    # Simulate a directory replacing an already-enumerated filename.
    monkeypatch.setattr(native, "os", SimpleNamespace(walk=lambda *args, **kw: [(str(tmp_path), [], [candidate.name])]))
    with pytest.raises(ValueError, match="Only regular files"):
        native._content_files(tmp_path)
    candidate.rmdir()
    with pytest.raises(FileNotFoundError):
        native._content_files(tmp_path)


def test_worker_can_finish_during_detach_and_escaped_wrapper_returns_to_native(native, installed, tmp_path, monkeypatch):
    target = tmp_path / "teams.json"
    target.write_text("{}")
    entered, release = threading.Event(), threading.Event()
    original, wrapper = Path.lstat, native._content_files
    def waiting(path, *args, **kwargs):
        if path == target and not entered.is_set():
            entered.set()
            assert release.wait(3)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", waiting)
    results, errors = [], []
    def run():
        try:
            results.append(wrapper(tmp_path))
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(2)
        installed.stop()
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and not errors and results == [[target]]
    assert wrapper(tmp_path) == ["native validation"]


def test_unknown_native_implementation_stays_native_and_reports_warning(native, tmp_path):
    records, original = [], native._content_files
    adapter = WorkshopMetadata(SimpleNamespace(submit_log=lambda record, **kw: records.append(record)))
    try:
        assert native._content_files is original
        assert native._content_files(tmp_path) == ["native validation"]
        assert adapter.status()["groups"]["workshop.metadata"] == "failed"
        assert len(records) == 1 and records[0]["level"] == "WARNING"
        assert "implementation changed" in records[0]["detail"]
    finally:
        adapter.stop()


def test_changed_link_policy_keeps_native_behaviour_and_cleanup_restores_reference(native, installed, tmp_path, monkeypatch):
    original = native._content_files.__wrapped__
    monkeypatch.setattr(native, "_is_link", lambda path: False)
    assert native._content_files(tmp_path) == ["native validation"]
    installed.stop()
    assert native._content_files is original


@pytest.mark.parametrize("compatible", [False, True])
def test_unavailable_reporting_cannot_prevent_validation(native, tmp_path, monkeypatch, compatible):
    if compatible:
        monkeypatch.setattr(WorkshopMetadata, "CODE_DIGESTS", {
            name: WorkshopMetadata.code_digest(getattr(native, name).__code__)
            for name in WorkshopMetadata.CODE_DIGESTS})
    failures = []
    def unavailable(record, *, error=None):
        if "exception_snapshot" in record:
            # The retained warning and outbox now carry the same plain snapshot.
            # Native callbacks never need to pass a live exception to a sink.
            assert "traceback" not in record
            failures.append(record["exception_snapshot"])
        raise OSError("Fixture diagnostic channel unavailable")
    target = tmp_path / "teams.json"
    target.write_text("{}")
    adapter = WorkshopMetadata(SimpleNamespace(submit_log=unavailable))
    try:
        assert native._content_files(tmp_path) == ([target] if compatible else ["native validation"])
        assert bool(failures) is not compatible
        if not compatible:
            assert adapter.error_record["level"] == "WARNING" and adapter.errors
    finally:
        adapter.stop()
