# SPDX-License-Identifier: MPL-2.0
"""Keep compiled-code discovery bounded and free of author lookup/call effects."""

from types import ModuleType

import pytest

from hook_manager.profiling_discovery import code_inventory, loaded_bindings, DiscoveryError


def loaded(text):
    module = ModuleType("engine")
    root = compile(text, "<owned-test-module>", "exec")
    exec(root, module.__dict__)
    return root, module


def test_inventory_is_inert_and_distinguishes_nested_functions_and_lambdas():
    root = compile("""
class Car:
    def update(self):
        def nested(): return 1
        return nested
left, right = lambda: 1, lambda: 2
async def coroutine(): return 3
def generator(): yield 4
raise RuntimeError('must not execute')
""", "C:/private/source.py", "exec")
    rows, codes = code_inventory((root,))
    assert len(rows) == len(codes) == 6
    assert {row[0] for row in rows} >= {"Car.update", "Car.update.<locals>.nested", "<lambda>"}
    assert {row[3] for row in rows} == {"function", "generator", "coroutine"}
    assert len({row[2] for row in rows}) == 6
    assert all(codes[row[2]].co_qualname == row[0] for row in rows)
    assert "private" not in repr(rows)


def test_binding_lookup_handles_methods_and_aliases_without_properties_or_calls():
    root, module = loaded("""
def original(): raise AssertionError('do not call')
alias = original
class Car:
    def update(self): raise AssertionError('do not call')
    @staticmethod
    def static(): raise AssertionError('do not call')
    @classmethod
    def factory(cls): raise AssertionError('do not call')
    @property
    def dangerous(self): raise AssertionError('do not evaluate')
Car.Loop = Car
""")
    rows, codes = code_inventory((root,))
    found = loaded_bindings(module, codes)
    by_name = {codes[key].co_qualname: functions for key, functions in found.items()}
    assert set(by_name) == {"original", "Car.update", "Car.static", "Car.factory"}
    assert by_name["original"] == (module.original,)
    assert "Car.dangerous" in {r[0] for r in rows}  # Metadata, not proven visible binding.


def test_custom_instances_descriptors_and_metaclasses_are_never_queried():
    root, module = loaded("""
class TrapMeta(type):
    def __getattribute__(self, name): raise AssertionError('metaclass lookup')
    @property
    def __dict__(self): raise AssertionError('metaclass dictionary')
class Car(metaclass=TrapMeta):
    def update(self): return 1
class Trap:
    def __getattribute__(self, name): raise AssertionError('instance lookup')
    def __get__(self, *args): raise AssertionError('descriptor lookup')
    def __call__(self): raise AssertionError('callable lookup')
instance = Trap()
Car.descriptor = instance
""")
    _, codes = code_inventory((root,))
    found = loaded_bindings(module, codes)
    assert "Car.update" in {codes[key].co_qualname for key in found}


def test_code_identity_defeats_forged_names_and_module_labels():
    root, module = loaded("def selected(): return 1")
    foreign_root, foreign = loaded("def selected(): return 1")
    assert module.selected.__code__ is not foreign.selected.__code__
    foreign.selected.__module__ = "engine"
    foreign.selected.__qualname__ = "selected"
    module.foreign = foreign.selected
    module.selected.__wrapped__ = foreign.selected
    _, codes = code_inventory((root,))
    found = loaded_bindings(module, codes)
    assert tuple(found.values()) == ((module.selected,),)
    assert tuple(loaded_bindings(module, code_inventory((foreign_root,))[1]).values()) == ((foreign.selected,),)


def test_distinct_closures_are_preserved_and_shared_code_roots_deduplicate():
    root, module = loaded("""
def factory(value):
    def inner(): return value
    return inner
first, second = factory(1), factory(2)
""")
    rows, codes = code_inventory((root, root))
    assert len(rows) == 2
    found = loaded_bindings(module, codes)
    inner = next(key for key, code in codes.items() if code.co_name == "inner")
    assert found[inner] == (module.first, module.second)


@pytest.mark.parametrize("options", [
    {"maximum_functions": 1}, {"maximum_objects": 1}, {"maximum_constants": 1},
    {"maximum_functions": True},
])
def test_code_inventory_limits_fail_instead_of_returning_a_truncated_set(options):
    root = compile("def first(): pass\ndef second(): pass", "<owned>", "exec")
    with pytest.raises(DiscoveryError):
        code_inventory((root,), **options)


def test_loaded_namespace_limits_and_module_subclasses_are_explicit():
    root, module = loaded("class Outer:\n class Inner:\n  def method(self): pass")
    _, codes = code_inventory((root,))
    with pytest.raises(DiscoveryError, match="member"):
        loaded_bindings(module, codes, maximum_members=1)
    with pytest.raises(DiscoveryError, match="depth"):
        loaded_bindings(module, codes, maximum_depth=1)
    class CustomModule(ModuleType):
        def __getattribute__(self, name): raise AssertionError("module lookup")
    with pytest.raises(DiscoveryError):
        loaded_bindings(CustomModule("foreign"), codes)


def test_inventory_rejects_custom_constant_tuple_without_iterating_it():
    class CustomConstants(tuple):
        def __iter__(self):
            raise AssertionError("author iterator must not run")

    root = compile("def selected(): pass", "<owned>", "exec")
    root = root.replace(co_consts=CustomConstants(root.co_consts))
    with pytest.raises(DiscoveryError, match="ordinary tuple"):
        code_inventory((root,))


def test_replacement_changes_private_binding_without_modifying_old_references():
    root, module = loaded("def selected(): return 1")
    _, codes = code_inventory((root,))
    first = loaded_bindings(module, codes)
    original = module.selected
    replacement_root = compile("def selected(): return 2", "<replacement>", "exec")
    exec(replacement_root, module.__dict__)
    assert loaded_bindings(module, codes) == {}
    assert tuple(first.values()) == ((original,),)
    assert tuple(loaded_bindings(module, code_inventory((replacement_root,))[1]).values()) == ((module.selected,),)
