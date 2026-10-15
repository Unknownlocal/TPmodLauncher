# SPDX-License-Identifier: MPL-2.0
"""Real runner compilation, controller adoption and deterministic cancellation races."""

from concurrent.futures import CancelledError, ThreadPoolExecutor, wait
import shutil
import threading

import pytest

from launcher.compilation_cache import CompilationCache
from launcher.source_mod import SourceMod
from mod_runtime.bundle import BundleError, RunnerBundle
from mod_runtime import preparation
from mod_runtime.preparation import SourcePreparation
from mod_runtime.source import SourceError, SourceSnapshot


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


def completed(job):
    # Test synchronization only: production controllers poll take(), never wait.
    assert wait([job._future], timeout=30).done, "Preparation did not finish"


def test_real_preparation_runs_off_thread_then_adopts_on_controller(runner_bundle, source_folder, tmp_path, monkeypatch):
    owner = threading.get_ident()
    phases, compiled_results = [], []
    read, component, compile_runner = SourceSnapshot.read, runner_bundle.component, preparation.CompiledMod
    def snapshot(folder):
        phases.append(("snapshot", threading.get_ident()))
        return read(folder)
    def runner_bytes():
        phases.append(("runner", threading.get_ident()))
        return component()
    def compile_bytes(*args, **kwargs):
        phases.append(("compile", threading.get_ident()))
        result = compile_runner(*args, **kwargs)
        compiled_results.append(result)
        return result
    monkeypatch.setattr(SourceSnapshot, "read", snapshot)
    monkeypatch.setattr(runner_bundle, "component", runner_bytes)
    monkeypatch.setattr(preparation, "CompiledMod", compile_bytes)
    reports, cleaned = [], []
    with ThreadPoolExecutor(max_workers=1) as executor:
        with SourcePreparation(executor, runner_bundle, source_folder, cache=CompilationCache(tmp_path)) as job:
            completed(job)
            with job.take() as result:
                assert all(thread != owner for _, thread in phases)
                assert [phase for phase, _ in phases] == ["snapshot", "runner", "compile"]
                assert result.compiled.cache_status["state"] == "enabled"
                assert set(result.timings) == {"source_snapshot", "runner_read", "compile"}
                assert all(seconds >= 0 for seconds in result.timings.values())
                job.close()  # Ownership already belongs to result, not the job.
                assert not result.compiled.closed
                with pytest.raises(RuntimeError, match="already taken"):
                    job.take()
                (source_folder / "src/mod.py").write_text('raise AssertionError("changed on disk")')
                with SourceMod(runner_bundle, result.snapshot, prepared=result.compiled, timings=result.timings,
                               report=lambda *args: reports.append(args), cleanup=lambda: cleaned.append(True)) as mod:
                    assert mod.sandbox._thread == owner
                    assert result.compiled.data is None and reports == []
                    assert phases[-1] == ("runner", owner)  # Reverification at admission.
                    mod.activate()
                    assert mod.event("owned.event", {"ok": True}) == {"ok": True}
                assert result.timings["initialize"] >= 0
    assert reports == [("owned.prepared", "prepared source")] and cleaned == [True]
    assert result.snapshot is None and compiled_results[0].closed


def test_preparation_keeps_author_syntax_inert_until_controller_loading(runner_bundle, source_folder):
    (source_folder / "src/mod.py").write_text("def invalid(:")
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        completed(job)
        with job.take() as result:
            assert dict(result.snapshot.files)["mod.py"] == "def invalid(:"
            from launcher.sandbox import SandboxError
            with pytest.raises(SandboxError, match="SyntaxError"):
                SourceMod(runner_bundle, result.snapshot, prepared=result.compiled, report=lambda *_: None)
            assert result.compiled.closed


def test_queued_cancel_and_pending_take_never_read_compile_or_wait(source_folder, monkeypatch):
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
            with SourcePreparation(executor, None, source_folder) as job:
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


@pytest.mark.parametrize("phase", ["snapshot", "runner"])
def test_cancel_during_read_skips_subsequent_compilation(runner_bundle, source_folder, monkeypatch, phase):
    entered, release = threading.Event(), threading.Event()
    original = SourceSnapshot.read if phase == "snapshot" else runner_bundle.component
    def blocked(*args):
        result = original(*args)
        entered.set()
        assert release.wait(10), "Test did not release read"
        return result
    def forbidden(*args, **kwargs):
        pytest.fail("Cancellation checkpoint must skip compilation")
    if phase == "snapshot":
        monkeypatch.setattr(SourceSnapshot, "read", blocked)
    else:
        monkeypatch.setattr(runner_bundle, "component", blocked)
    monkeypatch.setattr(preparation, "CompiledMod", forbidden)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        future = job._future
        try:
            assert entered.wait(5)
            job.close()
            assert future.running()  # close returned without waiting for the read.
        finally:
            release.set()
    assert isinstance(future.exception(), CancelledError)


def test_cancel_during_native_compile_closes_result_at_checkpoint(runner_bundle, source_folder, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    results = []
    original = preparation.CompiledMod
    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        results.append(result)
        entered.set()
        assert release.wait(10), "Test did not release compiler"
        return result
    monkeypatch.setattr(preparation, "CompiledMod", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        future = job._future
        try:
            assert entered.wait(30)
            job.close()
            assert future.running() and not results[0].closed
        finally:
            release.set()
    assert isinstance(future.exception(), CancelledError) and results[0].closed


def test_cancel_after_last_checkpoint_disposes_late_success(runner_bundle, source_folder, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    results = []
    original = preparation._prepare
    def blocked(*args):
        result = original(*args)
        results.append(result)
        entered.set()
        assert release.wait(10), "Test did not release prepared result"
        return result
    monkeypatch.setattr(preparation, "_prepare", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        future = job._future
        try:
            assert entered.wait(30)
            job.close()
            assert future.running()
        finally:
            release.set()
    assert future.exception() is None
    assert results[0].snapshot is None and results[0].compiled.closed


def test_abandoning_ready_result_releases_native_resources_and_snapshot(runner_bundle, source_folder):
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        completed(job)
        result = job._future.result()
        job.close()
        job.close()
        assert result.snapshot is None and result.compiled.closed
        with pytest.raises(RuntimeError, match="closed"):
            result.__enter__()


@pytest.mark.parametrize("failure", ["source", "runner", "compile", "interrupt"])
@pytest.mark.parametrize("abandon", [False, True])
def test_worker_errors_propagate_on_take_and_are_safe_to_abandon(runner_bundle, source_folder, monkeypatch, failure, abandon):
    expected = (ValueError, SourceError)
    if failure == "source":
        (source_folder / "mod.toml").write_text("not TOML")
    elif failure == "runner":
        def fail():
            raise BundleError("changed bundle")
        monkeypatch.setattr(runner_bundle, "component", fail)
    elif failure == "compile":
        # Pass the bounded Component header check and fail the native validator.
        from wasmtime import WasmtimeError
        expected = (WasmtimeError,)
        monkeypatch.setattr(runner_bundle, "component", lambda: b"\0asm\x0d\0\x01\0\xff")
    else:
        expected = (KeyboardInterrupt,)
        def interrupt(*args):
            raise KeyboardInterrupt("owned worker interruption")
        monkeypatch.setattr(SourceSnapshot, "read", interrupt)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        completed(job)
        if abandon:
            job.close()  # Failed futures, including BaseException, must not escape cleanup.
        else:
            with pytest.raises(expected):
                job.take()
        with pytest.raises(RuntimeError, match="closed"):
            job.take()


def test_failure_after_compilation_releases_compiler_even_for_base_exception(runner_bundle, source_folder, monkeypatch):
    results = []
    original = preparation.CompiledMod
    def record(*args, **kwargs):
        result = original(*args, **kwargs)
        results.append(result)
        return result
    def interrupt(*args):
        raise KeyboardInterrupt("owned publication interruption")
    monkeypatch.setattr(preparation, "CompiledMod", record)
    monkeypatch.setattr(preparation, "PreparedSource", interrupt)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        completed(job)
        with pytest.raises(KeyboardInterrupt, match="publication"):
            job.take()
    assert results[0].closed


def test_changed_runner_is_rejected_when_prepared_result_is_adopted(runner_bundle, source_folder, tmp_path):
    shutil.copytree(runner_bundle.directory, tmp_path / "bundle")
    bundle = RunnerBundle(tmp_path / "bundle")
    cleaned = []
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, bundle, source_folder) as job:
        completed(job)
        with job.take() as result:
            (bundle.directory / "runner.wasm").write_bytes(b"changed after preparation")
            with pytest.raises(BundleError):
                SourceMod(bundle, result.snapshot, prepared=result.compiled,
                          report=lambda *_: pytest.fail("No guest should run"), cleanup=lambda: cleaned.append(True))
            assert result.compiled.closed and cleaned == [True]


def test_take_and_close_require_controller_thread_without_losing_ownership(runner_bundle, source_folder):
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        completed(job)
        for action in (job.take, job.close):
            with pytest.raises(RuntimeError, match="creating thread"):
                executor.submit(action).result(timeout=5)
        with job.take() as result:
            assert not result.compiled.closed
    assert result.compiled.closed


def test_executor_rejection_starts_no_preparation(source_folder):
    executor = ThreadPoolExecutor(max_workers=1)
    executor.shutdown()
    with pytest.raises(RuntimeError, match="cannot schedule"):
        SourcePreparation(executor, None, source_folder)


def test_relative_folder_is_bound_before_queued_worker_starts(runner_bundle, source_folder, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def occupy():
        entered.set()
        assert release.wait(10), "Test did not release executor"
    monkeypatch.chdir(tmp_path)
    other = tmp_path / "other working directory"
    other.mkdir()
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(occupy)
        assert entered.wait(5)
        try:
            with SourcePreparation(executor, runner_bundle, source_folder.name) as job:
                monkeypatch.chdir(other)
                release.set()
                completed(job)
                with job.take() as result:
                    assert result.snapshot.manifest.id == "owned.prepared"
        finally:
            release.set()


def test_cancelled_job_does_not_close_executor_or_replace_later_snapshot(runner_bundle, source_folder, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SourceSnapshot.read
    reads = []
    def blocked(folder):
        result = original(folder)
        reads.append(result)
        if len(reads) == 1:
            entered.set()
            assert release.wait(10), "Test did not release first snapshot"
        return result
    monkeypatch.setattr(SourceSnapshot, "read", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as old:
        try:
            assert entered.wait(5)
            old.close()
            path = source_folder / "mod.toml"
            path.write_text(path.read_text().replace("owned.prepared", "owned.replacement"))
            with SourcePreparation(executor, runner_bundle, source_folder) as new:
                release.set()
                completed(new)
                with new.take() as result:
                    assert result.snapshot.manifest.id == "owned.replacement"
                    assert result.snapshot is not reads[0]
        finally:
            release.set()
