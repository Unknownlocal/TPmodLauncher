# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for target."""

from functools import wraps
import importlib
import inspect
import sys
import threading
from types import SimpleNamespace

import pytest

from hook_manager.callables import CallBinding, CallableAdapters, CallableTarget, method_group
from hook_manager.manager import HookError

from .support import approve, boolean, project


def test_inherited_decorated_target_preserves_native_execution(host):
    events = []

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            events.append("enter")
            try:
                return function(*args, **kwargs)
            finally:
                events.append("exit")
        return wrapped

    class Parent:
        @decorate
        def method(self, value=1):
            events.append(value)
            return True

    class Child(Parent):
        pass

    module, engine = host()
    module.Child = Child
    original = inspect.getattr_static(Child, "method")
    approve(engine, module, "method", ("Child",))
    engine.register("owner", [CallBinding("rule", "method", project, boolean())])
    assert Child().method()
    assert events == ["enter", 1, "exit"]
    engine.remove_owner("owner")
    assert "method" not in vars(Child)
    assert inspect.getattr_static(Child, "method") is original
    engine.evaluate = lambda *args: [{"decision": "deny", "message": "No"}]
    engine.register("owner", [CallBinding("rule", "method", project, boolean())])
    assert Child().method() is False and events == ["enter", 1, "exit"]


def test_worker_import_defers_readiness_until_main_thread_poll(tmp_path, monkeypatch):
    name = "owned_deferred_private"
    source = "def method(value):\n    return True\n"
    (tmp_path / (name + ".py")).write_text(source)
    monkeypatch.syspath_prepend(str(tmp_path))
    engine = CallableAdapters(lambda *args: [{"decision": "deny", "message": "No"}])
    engine.approve(CallableTarget("method", name, (), "method", "(value)"))
    try:
        engine.register("owner", [CallBinding("rule", "method", project, boolean())])
        assert name not in sys.modules and engine.owners == {"owner": "pending"}
        thread = threading.Thread(target=lambda: importlib.import_module(name))
        thread.start()
        thread.join(3)
        assert engine.owners == {"owner": "pending"}
        engine.poll()
        assert engine.owners == {"owner": "ready"}
        module = sys.modules[name]
        assert module.method(1) is False
        engine.stop()
        assert module.method(1) is True and engine.finder not in sys.meta_path
    finally:
        engine.stop()
        sys.modules.pop(name, None)


def test_signature_targets_accept_body_changes_and_reject_signature_changes(host):
    module, engine = host()
    module.method = lambda value: True
    approve(engine, module)
    module.method = lambda value: False
    current = module.method
    events = []

    @wraps(current)
    def decorated(*args, **kwargs):
        events.append("native decorator")
        return current(*args, **kwargs)

    module.method = decorated
    engine.register("owner", [CallBinding("rule", "method", project, boolean())])
    assert module.method(1) is False and events == ["native decorator"]
    assert engine.owners == {"owner": "ready"}
    engine.remove_owner("owner")
    assert module.method is decorated
    module.method = lambda value, added: True
    with pytest.raises(HookError, match="signature changed"):
        engine.register("owner", [CallBinding("rule", "method", project, boolean())])
    assert not engine.entries


def test_helper_signatures_accept_new_bodies_and_check_defaults():
    def helper(self, value=1, *, option=True):
        return value

    owner = SimpleNamespace(helper=helper)
    expected = {"helper": "(self, value=1, *, option=True)"}
    assert method_group(owner, expected)["helper"] == (helper, False)
    offset = 2

    def replacement(self, value=1, *, option=True):
        return value + offset

    owner.helper = replacement
    assert method_group(owner, expected)["helper"] == (replacement, False)
    replacement.__defaults__ = (2,)
    with pytest.raises(HookError, match="signature changed"):
        method_group(owner, expected)
    replacement.__defaults__ = (1,)
    replacement.__kwdefaults__ = {"option": False}
    with pytest.raises(HookError, match="signature changed"):
        method_group(owner, expected)
    replacement.__kwdefaults__ = {"option": True}
    owner.helper = staticmethod(replacement)
    assert method_group(owner, expected)["helper"] == (replacement, True)
