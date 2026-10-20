# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for target."""

from functools import wraps
import importlib
import inspect
import marshal
import sys
import threading
from types import SimpleNamespace

import pytest

from hook_manager.callables import CallBinding, CallableAdapters, CallableTarget, code_digest, method_group
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
    code = compile(source, str(tmp_path / (name + ".py")), "exec").co_consts[0]
    engine = CallableAdapters(lambda *args: [{"decision": "deny", "message": "No"}])
    engine.approve(CallableTarget("method", name, (), "method", "(value)", (code_digest(code),)))
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


def test_code_fingerprints_survive_serialization_and_reject_body_drift(host):
    source = ("def method(value):\n"
              "    def nested(): return True\n"
              "    try: return nested() if value else False\n"
              "    except ValueError: return False\n")
    code = compile(source, "owned.py", "exec").co_consts[0]
    assert code_digest(code) == code_digest(marshal.loads(marshal.dumps(code)))
    moved = compile("\n\n" + source.replace("    try:", "\n    try:"), "different-build/owned.py", "exec").co_consts[0]
    assert code_digest(code) == code_digest(moved)
    for changed in (source.replace("return True", "return False"), source.replace("ValueError", "TypeError")):
        assert code_digest(code) != code_digest(compile(changed, "owned.py", "exec").co_consts[0])
    module, engine = host()
    module.method = lambda value: True
    approve(engine, module)
    module.method = lambda value: False
    original = module.method
    with pytest.raises(HookError, match="identity changed"):
        engine.register("owner", [CallBinding("rule", "method", project, boolean())])
    assert module.method is original and not engine.entries


def test_helper_identity_includes_defaults_keyword_defaults_and_descriptor_kind():
    def helper(self, value=1, *, option=True):
        return value
    owner = SimpleNamespace()
    owner.helper = helper
    _, original = method_group(owner, ("helper",))
    helper.__defaults__ = (2,)
    _, changed_default = method_group(owner, ("helper",))
    helper.__kwdefaults__ = {"option": False}
    _, changed_keyword = method_group(owner, ("helper",))
    owner.helper = staticmethod(helper)
    _, changed_descriptor = method_group(owner, ("helper",))
    assert len({original, changed_default, changed_keyword, changed_descriptor}) == 4
