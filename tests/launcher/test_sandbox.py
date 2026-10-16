# SPDX-License-Identifier: MPL-2.0
"""Exercise the real sandbox host with owned, including deliberately hostile, Components."""

from dataclasses import replace
from pathlib import Path
import threading
import time

import pytest
from wasmtime import wat2wasm

from launcher import sandbox as implementation
from launcher.sandbox import Limits, Operation, Sandbox, SandboxError

FIXTURES = Path(__file__).with_name("fixtures") / "sandbox"


@pytest.fixture
def guest():
    return bytes(wat2wasm((FIXTURES / "functions.wat").read_text()))


def grants(callback=lambda value: value + 6):
    # Only test infrastructure supplies host callbacks; guest bytes cannot supply Python code.
    return {"bonus": Operation(("s32",), "s32", callback)}


def test_instances_have_private_memory_and_copied_grants(guest):
    capabilities = grants()
    with Sandbox(guest, capabilities, limits=Limits(host_calls=1)) as first, Sandbox(
        guest, grants(lambda value: value + 2)
    ) as second:
        capabilities.clear()
        assert first.call("counter") == 1
        assert first.call("counter") == 2
        assert second.call("counter") == 1
        assert first.call("apply", 3) == 9
        assert first.call("apply", 3) == 9  # The operation budget resets for each call.
        assert second.call("apply", 3) == 5
        first.close()
        assert second.call("counter") == 2
    with pytest.raises(SandboxError, match="closed"):
        first.call("counter")


def test_phase_shares_fuel_across_exported_calls(guest):
    cleaned, completed = [], []
    with Sandbox(guest, grants(), cleanup=lambda: cleaned.append(True)) as sandbox:
        with pytest.raises(SandboxError, match="fuel"):
            with sandbox.phase(fuel=30, seconds=2):
                for _ in range(100):
                    completed.append(sandbox.call("counter"))
        assert 0 < len(completed) < 100 and sandbox.closed
    assert cleaned == [True]


def test_phase_allowance_does_not_extend_later_callback_fuel(guest):
    with Sandbox(guest, grants(), limits=Limits(fuel=1, initialization_fuel=1000)) as sandbox:
        with sandbox.phase(fuel=1000, seconds=2):
            assert sandbox.call("counter") == 1
        with pytest.raises(SandboxError, match="fuel"):
            sandbox.call("counter")
        assert sandbox.closed


def test_phase_shares_host_call_budget(guest):
    calls = []
    with Sandbox(guest, grants(lambda value: calls.append(value) or value), limits=Limits(host_calls=1)) as sandbox:
        with pytest.raises(SandboxError, match="Host operation budget"):
            with sandbox.phase(fuel=10000, seconds=2):
                sandbox.call("apply", 1)
                sandbox.call("apply", 2)
        assert calls == [1] and sandbox.closed


@pytest.mark.parametrize("last_call", [False, True])
def test_phase_deadline_counts_host_work_between_calls(guest, monkeypatch, last_call):
    with Sandbox(guest, grants()) as sandbox:
        now = [time.monotonic()]
        monkeypatch.setattr(implementation.time, "monotonic", lambda: now[0])
        with pytest.raises(SandboxError, match="deadline"):
            with sandbox.phase(fuel=10000, seconds=2):
                sandbox.call("counter")
                now[0] += 3
                if last_call:
                    sandbox.call("counter")
        assert sandbox.closed


def test_phase_epoch_stops_guest_with_plentiful_fuel(guest):
    with Sandbox(guest, grants()) as sandbox:
        with pytest.raises(SandboxError, match="interrupt|deadline"):
            with sandbox.phase(fuel=2**62, seconds=0.1):
                sandbox.call("loop")
        assert sandbox.closed


def test_phase_restores_normal_callback_deadline(guest):
    def slow(value):
        time.sleep(0.04)
        return value
    with Sandbox(guest, grants(slow), limits=Limits(seconds=0.01)) as sandbox:
        with sandbox.phase(fuel=10000, seconds=2):
            assert sandbox.call("apply", 3) == 3
        with pytest.raises(SandboxError, match="deadline|interrupt"):
            sandbox.call("apply", 3)
        assert sandbox.closed


@pytest.mark.parametrize("error", [KeyboardInterrupt("cancelled"), ValueError("host preparation failed")])
def test_host_failure_in_phase_cleans_once(guest, error):
    cleaned = []
    with Sandbox(guest, grants(), cleanup=lambda: cleaned.append(True)) as sandbox:
        with pytest.raises(type(error), match=str(error)):
            with sandbox.phase(fuel=10000, seconds=2):
                raise error
        assert sandbox.closed
    assert cleaned == [True]


def test_long_host_failure_keeps_bounded_context_and_cause(guest):
    def fail(value):
        raise ValueError("owned context: " + "x" * 1000 + " actual failure cause")
    with Sandbox(guest, grants(fail)) as sandbox:
        with pytest.raises(SandboxError) as failed:
            sandbox.call("apply", 3)
        text = str(failed.value)
        assert len(text) <= 512 and text.startswith("owned context:") and text.endswith("actual failure cause")
        assert sandbox.closed


def test_nested_phases_are_rejected(guest):
    with Sandbox(guest, grants()) as sandbox:
        with pytest.raises(SandboxError, match="Nested sandbox phases"):
            with sandbox.phase(fuel=10000, seconds=2):
                with sandbox.phase(fuel=10000, seconds=2):
                    pytest.fail("A nested phase cannot reset the aggregate budget")
        assert sandbox.closed


@pytest.mark.parametrize("kwargs", [{"fuel": True, "seconds": 1}, {"fuel": 0, "seconds": 1}, {"fuel": 1000, "seconds": float("nan")}])
def test_invalid_phase_budgets_fail_before_changing_instance(guest, kwargs):
    with Sandbox(guest, grants()) as sandbox:
        with pytest.raises(ValueError):
            with sandbox.phase(**kwargs):
                pytest.fail("Invalid budget")
        assert sandbox.call("counter") == 1


def test_unknown_import_is_rejected_even_if_never_called():
    # A valid start function would call bonus if admission incorrectly allowed the unknown import.
    source = (
        (FIXTURES / "startup.wat")
        .read_text()
        .replace("(component", '(component (import "wasi:filesystem/types@0.2.0" (func))', 1)
    )
    calls = []
    with pytest.raises(SandboxError, match="approved host operation"):
        Sandbox(bytes(wat2wasm(source)), grants(lambda value: calls.append(value)))
    assert calls == []


def test_import_signatures_and_resource_imports_are_checked_before_activation():
    for declaration in (
        '(func (param "value" string) (result s32))',
        '(instance (export "call" (func)))',
    ):
        component = bytes(wat2wasm(f'(component (import "bonus" {declaration}))'))
        with pytest.raises(SandboxError, match="approved host operation"):
            Sandbox(component, grants())


@pytest.mark.parametrize("payload", [b"\0asm\x01\0\0\0", "(component)"])
def test_native_or_noncomponent_inputs_are_not_loaded(payload):
    with pytest.raises(SandboxError, match="Component binary"):
        Sandbox(payload)


def test_component_byte_limit_prevents_compilation(guest):
    with pytest.raises(SandboxError, match="size-limited"):
        Sandbox(guest, grants(), limits=Limits(component_bytes=len(guest) - 1))


def test_initial_and_grown_memory_obey_the_same_store_limit(guest):
    limits = Limits(memory_bytes=2 * 65536)
    with Sandbox(guest, grants(), limits=limits) as sandbox:
        assert sandbox.call("grow", 1) == 1
        assert sandbox.call("grow", 1) == -1
        assert sandbox.call("apply", 3) == 9
    oversized = bytes(
        wat2wasm(
            "(component (core module $m (memory 3)) (core instance (instantiate $m)))"
        )
    )
    with pytest.raises(SandboxError, match="memory"):
        Sandbox(oversized, limits=limits)


@pytest.mark.parametrize(
    "function, message",
    [("trap", "unreachable"), ("loop", "fuel"), ("spam", "Host operation budget")],
)
def test_failed_call_disables_only_its_instance_and_cleans_once(
    guest, function, message
):
    cleaned = []
    with Sandbox(
        guest,
        grants(),
        limits=Limits(fuel=20_000, host_calls=3),
        cleanup=lambda: cleaned.append("closed"),
    ) as failed, Sandbox(guest, grants()) as healthy:
        with pytest.raises(SandboxError, match=message):
            failed.call(function)
        assert failed.closed and failed.failure
        assert cleaned == ["closed"]
        assert healthy.call("apply", 3) == 9
        with pytest.raises(SandboxError, match="closed"):
            failed.call("counter")
    assert cleaned == ["closed"]


@pytest.mark.parametrize("phase", ["startup", "post_return"])
@pytest.mark.parametrize("failure", ["trap", "loop"])
def test_start_and_post_return_are_bounded_and_cleaned(phase, failure):
    source = (FIXTURES / f"{phase}.wat").read_text()
    if failure == "loop":
        source = source.replace("unreachable", "(loop $forever (br $forever))")
    changes = []

    def callback(value):
        changes.append("temporary registration")
        return 0

    cleaned = []

    def cleanup():
        changes.clear()
        cleaned.append("closed")

    message = "unreachable" if failure == "trap" else "fuel"
    with pytest.raises(SandboxError, match=message):
        with Sandbox(
            bytes(wat2wasm(source)),
            grants(callback),
            limits=Limits(fuel=20_000),
            cleanup=cleanup,
        ) as sandbox:
            sandbox.call("answer")
    assert changes == [] and cleaned == ["closed"]


def test_epoch_deadline_interrupts_guest_even_with_plentiful_fuel(guest):
    with Sandbox(guest, grants(), limits=Limits(fuel=2**62, seconds=0.2)) as sandbox:
        with pytest.raises(SandboxError, match="interrupt|deadline"):
            sandbox.call("loop")
        assert sandbox.closed


@pytest.mark.parametrize("value", [True, 2**31, object()])
def test_non_scalar_or_out_of_range_arguments_disable_the_instance(guest, value):
    with Sandbox(guest, grants()) as sandbox:
        with pytest.raises(SandboxError, match="plain scalar"):
            sandbox.call("apply", value)
        assert sandbox.closed


def test_host_exceptions_and_invalid_results_disable_the_instance(guest):
    def fail(_):
        raise ValueError("owned operation failed")

    def interrupt(_):
        raise KeyboardInterrupt("owned cancellation")

    for callback in (fail, interrupt, lambda _: object()):
        with Sandbox(guest, grants(callback)) as sandbox:
            with pytest.raises(SandboxError):
                sandbox.call("apply", 3)
            assert sandbox.closed


def test_initial_host_exception_does_not_leak_into_a_later_instance(guest):
    source = (FIXTURES / "startup.wat").read_text()

    def fail(_):
        raise ValueError("initial host failure")

    with pytest.raises(SandboxError, match="initial host failure"):
        Sandbox(bytes(wat2wasm(source)), grants(fail))
    with Sandbox(guest, grants()) as healthy:
        with pytest.raises(SandboxError, match="unreachable"):
            healthy.call("trap")


def test_host_callback_cannot_enter_another_sandbox(guest):
    with Sandbox(guest, grants()) as other:
        with Sandbox(guest, grants(lambda _: other.call("counter"))) as caller:
            with pytest.raises(SandboxError, match="Nested or concurrent"):
                caller.call("apply", 3)
            assert caller.closed
        assert other.call("counter") == 1


def test_late_host_operation_is_rejected_without_claiming_preemption(guest):
    def slow(value):
        time.sleep(0.2)
        return value

    with Sandbox(guest, grants(slow), limits=Limits(seconds=0.1)) as sandbox:
        started = time.monotonic()
        with pytest.raises(SandboxError, match="deadline"):
            sandbox.call("apply", 3)
        assert time.monotonic() - started >= 0.2
        assert sandbox.closed


def test_startup_deadline_override_does_not_extend_exported_calls():
    source = (FIXTURES / "functions.wat").read_text().replace(
        '(global $counter',
        '(func $initialize i32.const 0 call $bonus drop) (start $initialize) '
        '(global $counter',
    )

    def slow(value):
        time.sleep(0.2)
        return value

    # The same operation fits the startup budget but exceeds the later call budget.
    component = bytes(wat2wasm(source))
    with Sandbox(
        component,
        grants(slow),
        limits=Limits(seconds=0.1, initialization_seconds=2.0),
    ) as sandbox:
        with pytest.raises(SandboxError, match="deadline"):
            sandbox.call("apply", 3)
        assert sandbox.closed
    # An override must also be enforced when it is shorter than the normal budget.
    with pytest.raises(SandboxError, match="deadline"):
        Sandbox(
            component,
            grants(slow),
            limits=Limits(seconds=2.0, initialization_seconds=0.1),
        )


def test_caught_nested_entry_still_disables_the_instance(guest):
    def reenter(value):
        with pytest.raises(SandboxError, match="Nested"):
            sandbox.call("counter")
        return value

    with Sandbox(guest, grants(reenter)) as sandbox:
        with pytest.raises(SandboxError, match="Nested"):
            sandbox.call("apply", 3)
        assert sandbox.closed


def test_other_threads_cannot_use_or_free_a_live_store(guest):
    errors = []
    with Sandbox(guest, grants()) as sandbox:

        def worker():
            for action in (lambda: sandbox.call("counter"), sandbox.close):
                try:
                    action()
                except SandboxError as error:
                    errors.append(str(error))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert len(errors) == 2 and all("creating thread" in error for error in errors)
        assert sandbox.call("counter") == 1


@pytest.mark.parametrize("size", [4096, 4097])
def test_guest_text_results_are_bounded(size):
    source = (
        (FIXTURES / "text.wat")
        .read_text()
        .replace("@TEXT@", "x" * size)
        .replace("@LENGTH@", str(size))
    )
    with Sandbox(bytes(wat2wasm(source))) as sandbox:
        if size == 4096:
            assert sandbox.call("text") == "x" * size
        else:
            with pytest.raises(SandboxError, match="plain scalar"):
                sandbox.call("text")
            assert sandbox.closed


def test_cleanup_failure_cannot_leave_the_instance_active_or_repeat_cleanup(guest):
    calls = []

    def cleanup():
        calls.append("cleanup")
        raise ValueError("owned cleanup failed")

    sandbox = Sandbox(guest, grants(), cleanup=cleanup)
    with pytest.raises(SandboxError, match="owned cleanup failed"):
        sandbox.close()
    assert sandbox.closed
    sandbox.close()
    assert calls == ["cleanup"]


@pytest.mark.parametrize(
    "setting",
    [
        {"fuel": 0},
        {"memory_bytes": True},
        {"host_calls": -1},
        {"seconds": float("nan")},
        {"seconds": threading.TIMEOUT_MAX * 2},
        {"initialization_seconds": 0},
        {"initialization_seconds": True},
    ],
)
def test_limits_cannot_be_disabled_by_invalid_configuration(setting):
    with pytest.raises(ValueError):
        replace(Limits(), **setting)
