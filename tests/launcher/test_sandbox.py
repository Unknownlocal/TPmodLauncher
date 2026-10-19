# SPDX-License-Identifier: MPL-2.0
"""Exercise the real sandbox host with owned, including deliberately hostile, Components."""

from pathlib import Path
import threading

import pytest
from wasmtime import wat2wasm

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


def test_import_signatures_and_resource_imports_are_checked_before_activation():
    for declaration in (
        '(func (param "value" string) (result s32))',
        '(instance (export "call" (func)))',
    ):
        component = bytes(wat2wasm(f'(component (import "bonus" {declaration}))'))
        with pytest.raises(SandboxError, match="approved host operation"):
            Sandbox(component, grants())


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


def test_epoch_deadline_interrupts_guest_even_with_plentiful_fuel(guest):
    with Sandbox(guest, grants(), limits=Limits(fuel=2**62, seconds=0.2)) as sandbox:
        with pytest.raises(SandboxError, match="interrupt|deadline"):
            sandbox.call("loop")
        assert sandbox.closed


def test_phase_call_budgets_refresh_without_restarting_the_action_watchdog(guest):
    with Sandbox(guest, grants(), limits=Limits(host_calls=1)) as sandbox:
        with pytest.raises(SandboxError, match="interrupt|deadline"):
            with sandbox.phase(fuel=1000, seconds=.05):
                assert sandbox.call("apply", 1, fuel=1000) == 7
                assert sandbox.call("apply", 2, fuel=1000) == 8
                # Fresh fuel/host-call counts cannot replace the phase deadline;
                # epoch interruption still stops a loop with effectively ample fuel.
                sandbox.call("loop", fuel=2**62)
        assert sandbox.closed and sandbox._phase_timer is None


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
