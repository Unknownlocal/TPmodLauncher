# SPDX-License-Identifier: MPL-2.0
"""Real runner compilation, controller adoption and deterministic cancellation races."""

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import ExitStack
import shutil
import threading

import pytest

from launcher import compilation_registry
from mod_runtime.bundle import BundleError, RunnerBundle
from mod_runtime import preparation
from mod_runtime.preparation import SourcePreparation
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceError, SourceManifest, SourceSnapshot


CODE = '''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self): wit_world.report("prepared source")
    def on_event(self, name, payload): return payload
'''


@pytest.fixture
def source_folder(tmp_path):
    folder = tmp_path / "owned source"
    (folder / "src").mkdir(parents=True)
    (folder / "mod.toml").write_text('''schema = 2
id = "owned.prepared"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics"]
''', encoding="utf-8")
    (folder / "src/mod.py").write_text(CODE, encoding="utf-8")
    return folder


@pytest.fixture
def source_group(source_folder, tmp_path):
    peer = tmp_path / "peer source"
    shutil.copytree(source_folder, peer)
    manifest = peer / "mod.toml"
    manifest.write_text(manifest.read_text().replace("owned.prepared", "owned.peer"), encoding="utf-8")
    return tuple((folder, SourceManifest.parse((folder / "mod.toml").read_bytes()))
                 for folder in (source_folder, peer))


def completed(job):
    # Test synchronization only: production controllers poll both stages.
    assert wait([job._future], timeout=30).done, "Preparation did not finish"
    result = job.take()
    if result is None:
        assert wait([job._result.compiled._entry.future], timeout=30).done, "Compiler did not finish"
        result = job.take()
    return result


def test_group_preparation_reads_runner_once_and_feeds_one_shared_store(runner_bundle, source_group, monkeypatch):
    owner = threading.get_ident()
    phases, reports = [], []
    read, component, compile_request = SourceSnapshot.read, runner_bundle.component, preparation.CompiledMod.__init__
    def snapshot(folder):
        phases.append(("snapshot", threading.get_ident()))
        return read(folder)
    def runner_bytes():
        phases.append(("runner", threading.get_ident()))
        return component()
    def request(instance, *args, **kwargs):
        phases.append(("request", threading.get_ident()))
        compile_request(instance, *args, **kwargs)
    monkeypatch.setattr(SourceSnapshot, "read", snapshot)
    monkeypatch.setattr(runner_bundle, "component", runner_bytes)
    monkeypatch.setattr(preparation.CompiledMod, "__init__", request)
    entry = source_group[0][0] / "src/mod.py"
    entry.write_text("import wit_world\nPEER = wit_world.import_mod('owned.peer', 'mod')\n"
                     "wit_world.report('source-import')\n" + CODE, encoding="utf-8")
    with ThreadPoolExecutor(max_workers=1) as executor, ExitStack() as runtime:
        with SourcePreparation(executor, runner_bundle, source_group) as job:
            with completed(job) as result:
                assert [phase for phase, _ in phases] == ["snapshot", "snapshot", "runner", "request"]
                assert all(thread != owner for _, thread in phases[:3]) and phases[-1][1] == owner
                assert reports == [] and result.component is None
                assert tuple(item.manifest for item in result.snapshots) == tuple(item[1] for item in source_group)
                assert set(result.timings) == {"source_snapshot", "runner_read", "compile"}
                job.close()  # Taking the result transfers ownership away from the job.
                assert not result.compiled.closed
                # Runner construction must use the checked copies, even if disk files change.
                entry.write_text("raise AssertionError('changed on disk')", encoding="utf-8")
                providers = {manifest.id: {"report": lambda text, mod=manifest.id: reports.append((mod, text))}
                             for _, manifest in source_group}
                group = runtime.enter_context(SharedPythonRunner(runner_bundle, result.snapshots, providers,
                                                                  prepared=result.compiled))
                assert group.sandbox._thread == owner and result.compiled.data is None
        assert result.snapshots is None and result.compiled.closed
        assert not group.closed  # The Store keeps the adopted code after input cleanup.
        assert phases[-1] == ("runner", owner) and len(phases) == 5  # Fresh bundle verification only.
        for member in group.members.values():
            group.activate(member.manifest.id)
            assert group.on_event(member.manifest.id, "probe", '{"ok":true}') == '{"ok":true}'
    assert group.closed
    assert reports == [("owned.prepared", "source-import"), ("owned.prepared", "prepared source"),
                       ("owned.peer", "prepared source")]


@pytest.mark.parametrize("failure", ["changed-manifest", "bytes", "files"])
def test_group_admission_failure_never_reads_or_compiles_runner(runner_bundle, source_group, monkeypatch, failure):
    def forbidden(*args, **kwargs):
        pytest.fail("Incomplete or oversized groups must not request runner code")
    monkeypatch.setattr(runner_bundle, "component", forbidden)
    monkeypatch.setattr(preparation, "CompiledMod", forbidden)
    if failure == "changed-manifest":
        path = source_group[1][0] / "mod.toml"
        path.write_text(path.read_text().replace('version = "0.1.0"', 'version = "0.2.0"'), encoding="utf-8")
        message = "manifest changed"
    else:
        limit = "MAX_GROUP_SOURCE_BYTES" if failure == "bytes" else "MAX_GROUP_SOURCE_FILES"
        # One member fits; the second crosses the group ceiling.
        first = SourceSnapshot.read(source_group[0][0])
        monkeypatch.setattr(preparation, limit, first.total_bytes if failure == "bytes" else len(first.files))
        message = "group exceeds its budget"
    with ThreadPoolExecutor(max_workers=1) as executor:
        with SourcePreparation(executor, runner_bundle, source_group) as job:
            with pytest.raises(SourceError, match=message):
                completed(job)
            with pytest.raises(RuntimeError, match="closed"):
                job.take()


def test_duplicate_group_members_are_rejected_before_submission(source_group):
    # No executor is needed: ambiguous identities fail before any work is queued.
    with pytest.raises(SourceError, match="Duplicate shared source owner"):
        SourcePreparation(None, None, (source_group[0], source_group[0]))


def test_queued_cancel_and_pending_take_never_read_compile_or_wait(source_group, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def occupy():
        entered.set()
        assert release.wait(10), "Test did not release executor"
    def forbidden(*args):
        pytest.fail("Cancelled queued preparation must not start")
    monkeypatch.setattr(SourceSnapshot, "read", forbidden)
    with ThreadPoolExecutor(max_workers=1) as executor:
        blocker = executor.submit(occupy)
        assert entered.wait(5)
        try:
            with SourcePreparation(executor, None, source_group) as job:
                future = job._future
                monkeypatch.setattr(future, "result", forbidden)
                assert job.take() is None
                job.close()
                job.close()
                assert future.cancelled() and not blocker.done()
                with pytest.raises(RuntimeError, match="closed"):
                    job.take()
        finally:
            release.set()


def test_cancel_during_compilation_disposes_late_result(runner_bundle, source_group, monkeypatch, disposable_source_compiler):
    entered, release = threading.Event(), threading.Event()
    results = []
    original = compilation_registry.CompiledComponent
    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        results.append(result)
        entered.set()
        assert release.wait(10), "Test did not release compiler"
        return result
    monkeypatch.setattr(compilation_registry, "CompiledComponent", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_group) as job:
        try:
            assert wait([job._future], timeout=30).done
            assert job.take() is None
            future = job._result.compiled._entry.future
            assert entered.wait(30)
            job.close()
            assert future.running() and not results[0].closed
        finally:
            release.set()
    assert future.exception() is None and results[0].closed


def test_cancel_after_last_checkpoint_disposes_entire_late_group(runner_bundle, source_group, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    results = []
    original = preparation._prepare_group
    def blocked(*args):
        result = original(*args)
        results.append(result)
        entered.set()
        assert release.wait(10), "Test did not release prepared result"
        return result
    monkeypatch.setattr(preparation, "_prepare_group", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_group) as job:
        future = job._future
        try:
            assert entered.wait(30)
            job.close()
            assert future.running()
        finally:
            release.set()
    assert future.exception() is None
    assert results[0].snapshots is None and results[0].component is None and results[0].compiled is None


@pytest.mark.parametrize("failure", ["compile", "interrupt"])
def test_worker_errors_propagate_and_close_the_job(runner_bundle, source_group, monkeypatch, failure):
    if failure == "compile":
        # Pass the bounded Component header check and fail the native validator.
        from wasmtime import WasmtimeError
        expected = (WasmtimeError,)
        monkeypatch.setattr(runner_bundle, "component", lambda: b"\0asm\x0d\0\x01\0\xff")
    else:
        expected = (KeyboardInterrupt,)
        def interrupt(*args):
            raise KeyboardInterrupt("owned worker interruption")
        monkeypatch.setattr(SourceSnapshot, "read", interrupt)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_group) as job:
        with pytest.raises(expected):
            completed(job)
        with pytest.raises(RuntimeError, match="closed"):
            job.take()


def test_changed_runner_is_rejected_when_prepared_result_is_adopted(runner_bundle, source_group, tmp_path):
    shutil.copytree(runner_bundle.directory, tmp_path / "bundle")
    bundle = RunnerBundle(tmp_path / "bundle")
    cleaned = []
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, bundle, source_group) as job:
        with completed(job) as result:
            (bundle.directory / "runner.wasm").write_bytes(b"changed after preparation")
            with pytest.raises(BundleError):
                SharedPythonRunner(bundle, result.snapshots,
                                   {item.manifest.id: {'report': lambda _: pytest.fail('No guest should run')}
                                    for item in result.snapshots}, prepared=result.compiled,
                                   cleanup=lambda: cleaned.append(True))
            assert result.compiled.closed and cleaned == [True]
