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
from mod_runtime.source import SourceSnapshot


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


def test_cancel_during_compilation_closes_result_at_checkpoint(runner_bundle, source_folder, monkeypatch, disposable_source_compiler):
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


def test_cancel_after_last_checkpoint_disposes_late_success(runner_bundle, source_folder, monkeypatch, disposable_source_compiler):
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


@pytest.mark.parametrize("failure", ["compile", "interrupt"])
def test_worker_errors_propagate_and_close_the_job(runner_bundle, source_folder, monkeypatch, failure):
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
    with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, runner_bundle, source_folder) as job:
        completed(job)
        with pytest.raises(expected):
            job.take()
        with pytest.raises(RuntimeError, match="closed"):
            job.take()


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
