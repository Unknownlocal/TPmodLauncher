# SPDX-License-Identifier: MPL-2.0
"""Shared decoding/selection semantics, independent of regulation admission."""

import pytest

from launcher.json_ingest import JsonObject, decode, materialize


def test_utf8_byte_limit_and_explicit_bom_policy():
    content = b'\xef\xbb\xbf{"text":"\xc3\xa9"}'
    raw = decode(content, max_bytes=len(content), allow_bom=True)
    assert raw.materialize() == {"text": "é"}
    with pytest.raises(ValueError, match="bytes of UTF-8 JSON"):
        decode(content, max_bytes=len(content) - 1, allow_bom=True)
    with pytest.raises(ValueError, match="Invalid JSON"):
        decode(content, max_bytes=len(content))


@pytest.mark.parametrize("content", [b'\xff', b'{', b'{"value":NaN}'],
                         ids=["encoding", "syntax", "non-json-constant"])
def test_invalid_encoding_syntax_and_constants_are_rejected(content):
    with pytest.raises(ValueError, match="Invalid JSON"):
        decode(content, max_bytes=100)


def test_generic_array_and_scalar_roots_materialize_without_a_document_schema():
    raw = decode(b'[null,true,2,1.5,"text",{"value":3}]', max_bytes=100)
    assert type(raw[-1]) is JsonObject
    assert materialize(raw) == [None, True, 2, 1.5, "text", {"value": 3}]
    assert materialize(decode(b'"scalar"', max_bytes=100)) == "scalar"


def test_indexed_selection_and_recursive_duplicate_policies():
    raw = decode(b'{"same":1,"same":2,"nested":{"x":3,"x":4},"good":5}', max_bytes=100)
    missing = object()
    assert raw.member("good") == 5 and raw.member("absent", missing) is missing
    assert raw.member("same", duplicates="last") == 2
    with pytest.raises(ValueError, match="Duplicate JSON field: same"):
        raw.member("same")
    with pytest.raises(ValueError, match="Duplicate JSON field: x"):
        materialize(raw.member("nested"))
    assert raw.materialize(exclude=("same", "nested")) == {"good": 5}
    assert raw.materialize(duplicates="last") == {"same": 2, "nested": {"x": 4}, "good": 5}


def test_selected_values_are_independent_and_excluded_values_remain_unvalidated():
    raw = decode(b'{"unused":1e400,"selected":{"items":[{"n":1}]}}', max_bytes=100)
    selected = materialize(raw.member("selected"))
    selected["items"][0]["n"] = 9
    assert raw.materialize(exclude=("unused",)) == {"selected": {"items": [{"n": 1}]}}
    with pytest.raises(ValueError, match="Non-finite JSON number"):
        raw.materialize()
