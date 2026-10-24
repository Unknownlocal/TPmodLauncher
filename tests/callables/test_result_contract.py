# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for result_contract."""


import pytest

from hook_manager.callables import CallBinding, ResultContract
from hook_manager.manager import HookError

from .support import approve, project


def test_structural_return_contract_supports_records_optional_and_exact_types():
    contract = ResultContract({"shape": {"record": {"ok": "bool", "reason": {"optional": "string"}}},
        "denied": {"record": {"ok": {"literal": False}, "reason": {"message": True}}},
        "accepted": {"path": ["ok"], "one_of": [True]}})
    assert contract.allowed({"ok": True, "reason": None})
    assert contract.denied("No") == {"ok": False, "reason": "No"}
    with pytest.raises(HookError):
        contract.allowed({"ok": 1, "reason": None})
    with pytest.raises(HookError, match="success"):
        ResultContract({"shape": "bool", "denied": {"literal": True},
                        "accepted": {"path": [], "one_of": [True]}})


def test_list_result_contract_checks_success_denial_and_bounds():
    codec = ResultContract({"shape": {"list": {"item": "string", "max_items": 8}},
        "denied": {"list": [{"message": True}]},
        "accepted": {"path": [], "one_of": [[]]}})
    assert codec.allowed([])
    assert not codec.allowed(["Native refusal"])
    assert codec.denied("Rule refusal") == ["Rule refusal"]
    with pytest.raises(HookError):
        codec.allowed(["reason"] * 9)


@pytest.mark.parametrize("shape,denied,path", [
    ("string", {"message": True}, []),
    ({"tuple": ["string", "int"]}, {"tuple": [{"message": True}, {"literal": 0}]}, [0]),
])
def test_denial_message_cannot_encode_native_success(host, shape, denied, path):
    codec = ResultContract({"shape": shape, "denied": denied,
                           "accepted": {"path": path, "one_of": ["OK"]}})
    assert not codec.allowed(codec.denied("OK"))
    module, engine = host(lambda *args: [{"decision": "deny", "message": "OK"}])
    module.method = lambda value: True
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", project, codec)])
    assert not codec.allowed(module.method(1))
    assert engine.owners == {"owner": "ready"}
