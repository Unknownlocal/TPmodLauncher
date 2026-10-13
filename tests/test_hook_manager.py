# SPDX-License-Identifier: MPL-2.0
"""Exercise reversible hooks on owned modules and normal Python imports."""

import importlib
import inspect
import sys
import threading
from types import ModuleType

import pytest

from hook_manager.manager import HookError, Manager, Target, data_copy


@pytest.fixture
def host(monkeypatch):
    module = ModuleType("owned_hook_fixture")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    managers = []

    def create(dispatch):
        manager = Manager(dispatch)
        managers.append(manager)
        return module, manager

    yield create
    for manager in managers:
        manager.stop()


def approve(manager, module, attribute, owners=(), *, replace=False, name=None):
    owner = module
    for part in owners:
        owner = getattr(owner, part)
    raw = inspect.getattr_static(owner, attribute)
    original = raw.__func__ if isinstance(raw, (staticmethod, classmethod)) else raw
    target = name or attribute
    manager.approve(
        Target(
            target,
            module.__name__,
            owners,
            attribute,
            str(inspect.signature(original)),
            replace=replace,
        )
    )
    return {"target": target, "callback": "owned.callback", "phase": "before"}


@pytest.mark.parametrize("phase", ["before", "after"])
def test_original_exceptions_propagate_once(host, phase):
    records, calls = [], []
    module, manager = host(
        lambda record: records.append(record) or {"action": "continue"}
    )
    error = ValueError("original failure")

    def method(value):
        calls.append(value)
        raise error

    module.method = method
    binding = approve(manager, module, "method")
    binding["phase"] = phase
    manager.install("owned", [binding])
    with pytest.raises(ValueError) as caught:
        module.method(3)
    assert caught.value is error and calls == [3]
    assert len(records) == (1 if phase == "before" else 0)
    assert manager.status()["groups"] == {"owned": "active"}


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize(
    "decision", [None, {"action": "replace", "value": 99}, object()]
)
def test_invalid_callbacks_restore_and_keep_original_behavior(host, phase, decision):
    module, manager = host(lambda record: decision)
    calls = []

    def method(value):
        calls.append(value)
        return value + 1

    module.method = method
    binding = approve(manager, module, "method")
    binding["phase"] = phase
    manager.install("owned", [binding])
    escaped_wrapper = module.method
    assert module.method(3) == 4 and calls == [3]
    assert module.method is method and manager.status()["groups"]["owned"] == "failed"
    assert escaped_wrapper(5) == 6 and calls == [3, 5]


def test_methods_descriptors_and_inherited_restore(host):
    records = []
    module, manager = host(
        lambda record: records.append(record) or {"action": "continue"}
    )

    class Parent:
        def method(self, value=2):
            return value + 1

    class Child(Parent):
        @staticmethod
        def static(value):
            return value * 2

        @classmethod
        def class_method(cls, value):
            return cls.__name__ + str(value)

    module.Child = Child
    originals = {
        name: inspect.getattr_static(Child, name)
        for name in ("method", "static", "class_method")
    }
    bindings = [approve(manager, module, name, ("Child",)) for name in originals]
    manager.install("owned", bindings)
    assert (
        Child().method() == 3
        and Child.static(4) == 8
        and Child.class_method(5) == "Child5"
    )
    assert [record["arguments"] for record in records] == [
        {"value": 2},
        {"value": 4},
        {"value": 5},
    ]
    assert manager.install("owned", bindings)["groups"] == {"owned": "active"}
    manager.remove("owned")
    assert "method" not in vars(Child)
    assert all(
        inspect.getattr_static(Child, name) is original
        for name, original in originals.items()
    )


@pytest.mark.parametrize("phase", ["before", "after"])
def test_explicit_replacement_permission(host, phase):
    module, manager = host(
        lambda record: {"action": "replace", "value": record["arguments"]["value"] * 4}
    )
    calls = []
    module.method = lambda value: calls.append(value) or value
    binding = approve(manager, module, "method", replace=True)
    binding["phase"] = phase
    manager.install("owned", [binding])
    assert module.method(3) == 12
    assert calls == ([3] if phase == "after" else [])


def test_conflicts_and_external_replacements_are_not_overwritten(host):
    module, manager = host(lambda record: {"action": "continue"})
    module.method = lambda value: value
    binding = approve(manager, module, "method")
    manager.install("owned", [binding])
    with pytest.raises(HookError):
        manager.install("other", [binding])
    external = lambda value: value + 10
    module.method = external
    manager.remove("owned")
    assert module.method is external and manager.status()["errors"]


def test_group_rollback_after_assignment_writes_then_raises(host):
    module, manager = host(lambda record: {"action": "continue"})

    class Guard(type):
        def __setattr__(cls, name, value):
            super().__setattr__(name, value)
            if name == "second" and hasattr(value, "_tpm_hook_group"):
                raise RuntimeError("rejected after write")

    class Owned(metaclass=Guard):
        def first(self):
            return 1

        def second(self):
            return 2

    module.Owned = Owned
    originals = [Owned.first, Owned.second]
    with pytest.raises(HookError, match="rejected after write"):
        manager.install(
            "owned",
            [
                approve(manager, module, name, ("Owned",))
                for name in ("first", "second")
            ],
        )
    assert [Owned.first, Owned.second] == originals


def test_signature_change_rejected_before_any_mutation(host):
    module, manager = host(lambda record: {"action": "continue"})
    module.first = lambda value: value
    module.second = lambda value: value
    bindings = [approve(manager, module, name) for name in ("first", "second")]
    first = module.first
    module.second = lambda value, added: added
    with pytest.raises(HookError, match="signature changed"):
        manager.install("owned", bindings)
    assert module.first is first


@pytest.mark.parametrize("worker", [False, True])
def test_deferred_import_installs_without_forcing_import(tmp_path, monkeypatch, worker):
    name = "owned_deferred_hook"
    (tmp_path / (name + ".py")).write_text("def method(value):\n    return value + 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    records = []
    manager = Manager(lambda record: records.append(record) or {"action": "continue"})
    manager.approve(Target("method", name, (), "method", "(value)"))
    manager.install(
        "owned", [{"target": "method", "callback": "owned.callback", "phase": "before"}]
    )
    assert name not in sys.modules
    try:
        if worker:
            thread = threading.Thread(target=importlib.import_module, args=(name,))
            thread.start()
            thread.join()
            assert manager.status()["groups"]["owned"] == "pending"
            manager.poll()
        else:
            importlib.import_module(name).method(1)
            assert (
                len(records) == 1
            )  # Installed before the import's consumer called the method.
        assert sys.modules[name].method(3) == 4
        manager.stop()
        assert manager.finder not in sys.meta_path
        assert not hasattr(sys.modules[name].method, "_tpm_hook_group")
    finally:
        manager.stop()
        sys.modules.pop(name, None)


def test_data_boundary_and_unconfigured_manager():
    for value in (object(), (1, 2), float("nan"), {"x": 2**64}, [0] * 1025):
        with pytest.raises(HookError):
            data_copy(value)
    original = {"nested": [1, "text"]}
    copied = data_copy(original)
    copied["nested"].append(2)
    assert original == {"nested": [1, "text"]}
    manager = Manager()
    manager.approve(Target("owned", "not_imported", (), "method", "()"))
    with pytest.raises(HookError, match="dispatcher"):
        manager.install(
            "owned", [{"target": "owned", "callback": "callback", "phase": "before"}]
        )
    with pytest.raises(HookError, match="Unknown"):
        manager.request({"operation": "execute"})
    assert manager.request({"operation": "shutdown"})["state"] == "stopped"


def test_worker_calls_bypass_host_callback_and_raw_arguments_fail_safely(host):
    records = []
    module, manager = host(
        lambda record: records.append(record) or {"action": "continue"}
    )
    module.method = lambda value: value
    original = module.method
    manager.install("owned", [approve(manager, module, "method")])
    results = []
    thread = threading.Thread(target=lambda: results.append(module.method(3)))
    thread.start()
    thread.join()
    assert results == [3] and records == []
    raw = object()
    assert module.method(raw) is raw and module.method is original
    assert manager.status()["groups"]["owned"] == "failed"


def test_dispatch_failure_or_removal_cannot_change_original_result(host):
    module, manager = host(None)
    module.method = lambda value: value + 1
    binding = approve(manager, module, "method", replace=True)

    def dispatch(record):
        manager.remove("owned")
        return {"action": "replace", "value": 90}

    manager.dispatch = dispatch
    manager.install("owned", [binding])
    assert module.method(3) == 4

    def broken(record):
        raise RuntimeError("callback failed")

    manager.dispatch = broken
    manager.install("next", [binding])
    assert module.method(3) == 4 and manager.status()["groups"]["next"] == "failed"


def test_alias_targets_rejected_together(host):
    module, manager = host(lambda record: {"action": "continue"})
    module.method = lambda value: value
    original = module.method
    bindings = [
        approve(manager, module, "method", name=name) for name in ("one", "two")
    ]
    with pytest.raises(HookError, match="same callable"):
        manager.install("owned", bindings)
    assert module.method is original


def test_failing_import_keeps_its_exception_and_never_installs(tmp_path, monkeypatch):
    name = "owned_failed_import"
    (tmp_path / (name + ".py")).write_text(
        "raise ValueError('original import error')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    manager = Manager(lambda record: {"action": "continue"})
    manager.approve(Target("method", name, (), "method", "()"))
    try:
        manager.install(
            "owned",
            [{"target": "method", "callback": "owned.callback", "phase": "before"}],
        )
        with pytest.raises(ValueError, match="original import error"):
            importlib.import_module(name)
        assert (
            name not in sys.modules and manager.status()["groups"]["owned"] == "pending"
        )
    finally:
        manager.stop()


@pytest.mark.parametrize("worker", [False, True])
def test_lazy_import_rejected_without_installing_or_forcing_execution(
    tmp_path, monkeypatch, worker
):
    name = "owned_lazy_hook"
    source = tmp_path / (name + ".py")
    marker = tmp_path / "executed"
    source.write_text(
        "from pathlib import Path\nPath("
        + repr(str(marker))
        + ").touch()\ndef method(value): return value\n"
    )

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == name:
                spec = importlib.util.spec_from_file_location(name, source)
                spec.loader = importlib.util.LazyLoader(spec.loader)
                return spec

    finder = Finder()
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    manager = Manager(lambda record: {"action": "continue"})
    manager.approve(Target("method", name, (), "method", "(value)"))
    try:
        manager.install(
            "owned",
            [{"target": "method", "callback": "owned.callback", "phase": "before"}],
        )
        if worker:
            thread = threading.Thread(target=importlib.import_module, args=(name,))
            thread.start()
            thread.join()
            manager.poll()
        else:
            importlib.import_module(name)
        assert not marker.exists()
        assert manager.status()["groups"]["owned"] == "failed"
        assert sys.modules[name].method(3) == 3 and marker.exists()
    finally:
        manager.stop()
        sys.modules.pop(name, None)
