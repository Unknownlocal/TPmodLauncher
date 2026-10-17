# SPDX-License-Identifier: MPL-2.0
"""Exercise inert discovery, source identity, live invalidation and bounded queries."""

import json

import pytest

from hook_manager.manager import data_copy
from launcher import profiling_catalogue as profiling
from mod_runtime.source import SourceManifest, SourceSnapshot


GAME = {"kind": "game"}
MOD = {"kind": "mod", "id": "example.ai"}


def source(text, module="physics", path="src/physics.py"):
    return profiling.Source(module, path, text.encode("utf-8"))


def query(catalogue, selectors=None, *, requester="diagnostics.one", cursor=None):
    request = ({"schema": 1, "cursor": cursor} if cursor is not None else
               {"schema": 1, "selectors": selectors or [{"owner": GAME}]})
    raw = catalogue.query(requester, json.dumps(request))
    data_copy(json.loads(raw))
    assert len(raw.encode("utf-8")) <= catalogue.limits.page_bytes
    return json.loads(raw)


def rows(catalogue, selectors=None):
    result = query(catalogue, selectors)
    found = []
    while True:
        assert result["ok"], result
        found.extend(result["data"]["functions"])
        token = result["data"]["next_cursor"]
        if token is None:
            return found
        result = query(catalogue, cursor=token)


def test_discovery_never_executes_source_and_preserves_nested_identities():
    catalogue = profiling.Catalogue("process-1")
    catalogue.replace(GAME, [source("""
class Car:
    def update(self):
        def child(): return 1
        return child
def gen(): yield 1
async def coro(): return 2
left, right = lambda: 1, lambda: 2
raise RuntimeError('must never execute')
""")])
    records = rows(catalogue)
    assert {r["qualified_name"] for r in records} >= {"Car.update", "Car.update.<locals>.child", "gen", "coro", "<lambda>"}
    assert len({r["function_id"] for r in records}) == len(records) == 6
    assert {r["kind"] for r in records} == {"function", "generator", "coroutine"}
    assert all(r["availability"] == "not_loaded" and r["metrics"] == [] for r in records)
    assert all(r["source"] == "src/physics.py" for r in records)
    assert all("<profiling-discovery>" not in json.dumps(r) for r in records)


def test_equal_sources_have_separate_owners_and_selectors_are_exact():
    catalogue = profiling.Catalogue("process-1")
    units = [source("def update(): return 1\ndef update_more(): return 2")]
    catalogue.replace(GAME, units)
    catalogue.replace(MOD, units)
    other = {"kind": "mod", "id": "example.other"}
    catalogue.replace(other, units)
    all_mods = rows(catalogue, [{"owner": {"kind": "mod"}}])
    assert len(all_mods) == 4
    selected = rows(catalogue, [{"owner": MOD, "module": "physics", "qualified_name": "update"}]*2)
    assert len(selected) == 1
    assert rows(catalogue, [{"owner": GAME, "function_id": selected[0]["function_id"]}]) == []
    assert len({r["function_id"] for r in rows(catalogue) + all_mods}) == 6


def test_persistent_cache_reuses_discovery_but_never_live_bindings(tmp_path, monkeypatch):
    cache = profiling.MetadataCache(tmp_path)
    first = profiling.Catalogue("process-1", cache=cache)
    units = [source("def update(): return 1")]
    (key,) = first.replace(GAME, units)
    first.bind(key, object(), runtime_kind="game-python", metrics=["calls", "elapsed"])
    assert rows(first)[0]["availability"] == "ready"
    fresh_cache = profiling.MetadataCache(tmp_path)
    monkeypatch.setattr(profiling, "_discover", lambda *a: pytest.fail("Cache hit recompiled source"))
    second = profiling.Catalogue("process-2", cache=fresh_cache)
    assert second.replace(GAME, units) == (key,)
    record = rows(second)[0]
    assert record["availability"] == "not_loaded" and record["binding_generation"] == "0"
    assert second.bindings == {}
    assert list(tmp_path.iterdir()) == [tmp_path / "catalogue.json"]


def test_checksum_and_manifest_changes_invalidate_only_affected_owner(tmp_path):
    catalogue = profiling.Catalogue("process-1", cache=profiling.MetadataCache(tmp_path))
    units = [source("def update(): return 1")]
    (game_key,) = catalogue.replace(GAME, units)
    (mod_key,) = catalogue.replace(MOD, units, identity=b"manifest-v1")
    catalogue.bind(mod_key, object(), runtime_kind="guest-python", metrics=["calls"])
    (new_key,) = catalogue.replace(MOD, units, identity=b"manifest-v2")
    assert new_key != mod_key and mod_key not in catalogue.rows and mod_key not in catalogue.bindings
    assert game_key in catalogue.rows
    (changed,) = catalogue.replace(MOD, [source("def update(): return 2")], identity=b"manifest-v2")
    assert changed != new_key


def test_readmission_and_rebinding_keep_source_identity_but_change_live_generation():
    catalogue = profiling.Catalogue("process-1")
    units = [source("def update(): return 1")]
    (key,) = catalogue.replace(GAME, units)
    binding = object()
    catalogue.bind(key, binding, runtime_kind="game-python", metrics=["calls"])
    generation = catalogue.generation
    assert catalogue.replace(GAME, units) == (key,)
    catalogue.bind(key, binding, runtime_kind="game-python", metrics=["calls"])
    assert catalogue.generation == generation
    catalogue.bind(key, object(), runtime_kind="game-python", metrics=["calls"])
    assert rows(catalogue)[0]["binding_generation"] == "2"
    catalogue.unbind(key, availability="unsupported", reason="resumable_unsupported")
    record = rows(catalogue)[0]
    assert record["availability"] == "unsupported" and record["metrics"] == []
    assert record["binding_generation"] == "3"


def test_snapshot_registration_uses_admitted_manifest_and_package_module_names():
    manifest = SourceManifest.parse(b'''schema=2
id="example.ai"
version="1.0.0"
api="0.3.0"
runtime="python"
entry="mod"
capabilities=["diagnostics"]
''')
    snapshot = SourceSnapshot(manifest, (("mod.py", "def run(): pass"), ("pkg/__init__.py", "def helper(): pass")), 100)
    catalogue = profiling.Catalogue("process-1")
    catalogue.replace_snapshot(snapshot)
    found = rows(catalogue, [{"owner": MOD}])
    assert {r["module"] for r in found} == {"mod", "pkg"}
    assert {r["source"] for r in found} == {"src/mod.py", "src/pkg/__init__.py"}


def test_query_paging_is_bounded_and_tokens_owned_expiring_single_use():
    clock = [1]
    catalogue = profiling.Catalogue("process-1", limits=profiling.Limits(page_bytes=1200, cursors=1), clock=lambda: clock[0])
    catalogue.replace(GAME, [source("\n".join(f"def function_{i}(): pass" for i in range(12)))])
    first = query(catalogue)
    token = first["data"]["next_cursor"]
    assert token
    assert query(catalogue, cursor=token, requester="other.mod")["error"]["code"] == "stale_catalogue"
    assert query(catalogue)["error"]["code"] == "resource_limit"
    second = query(catalogue, cursor=token)
    assert second["ok"]
    assert query(catalogue, cursor=token)["error"]["code"] == "stale_catalogue"
    clock[0] += 31
    assert query(catalogue, cursor=second["data"]["next_cursor"])["error"]["code"] == "stale_catalogue"
    catalogue.release("diagnostics.one")
    assert len(rows(catalogue)) == 12


def test_mutation_and_process_replacement_invalidate_cursors_and_release_refs():
    catalogue = profiling.Catalogue("process-1", limits=profiling.Limits(page_bytes=1200))
    catalogue.replace(GAME, [source("def one(): pass\ndef two(): pass\ndef three(): pass")])
    token = query(catalogue)["data"]["next_cursor"]
    (key, *_) = sorted(catalogue.rows)
    catalogue.bind(key, object(), runtime_kind="game-python", metrics=["calls"])
    assert query(catalogue, cursor=token)["error"]["code"] == "stale_catalogue"
    catalogue.close()
    assert not catalogue.rows and not catalogue.bindings and not catalogue.cursors
    assert query(catalogue)["error"]["code"] == "not_ready"
    replacement = profiling.Catalogue("process-2")
    assert query(replacement, cursor=token)["error"]["code"] == "stale_catalogue"


@pytest.mark.parametrize("text", [
    '{"schema":1,"schema":1}', '{"schema":true}', '{"schema":2}',
    '{"schema":1,"selectors":NaN}', '['*1100+']'*1100,
    '{"schema":1,"selectors":[],"extra":0}',
    '{"schema":1,"selectors":[{"owner":{"kind":"game"},"function_id":"x","module":"physics"}]}',
    '{"schema":1,"selectors":[{"owner":{"kind":"mod","id":"../bad"}}]}',
    '{"schema":1,"selectors":[{"owner":{"kind":"game"},"eval":"import x"}]}',
])
def test_invalid_queries_are_bounded_errors_without_registry_mutation(text):
    catalogue = profiling.Catalogue("process-1")
    catalogue.replace(GAME, [source("def update(): pass")])
    before = (catalogue.catalogue_id, catalogue.generation, dict(catalogue.rows))
    response = json.loads(catalogue.query("diagnostics.one", text))
    assert not response["ok"]
    assert (catalogue.catalogue_id, catalogue.generation, catalogue.rows) == before


def test_failed_registration_is_atomic_and_oversized_records_are_explicit():
    catalogue = profiling.Catalogue("process-1", limits=profiling.Limits(functions=1, page_bytes=1024))
    (key,) = catalogue.replace(GAME, [source("def update(): pass", module="m"*500, path="src/"+"p"*500+".py")])
    with pytest.raises(profiling.CatalogueError, match="functions"):
        catalogue.replace(GAME, [source("def one(): pass\ndef two(): pass")])
    assert list(catalogue.rows) == [key]
    assert query(catalogue)["error"]["code"] == "record_too_large"
    with pytest.raises(ValueError):
        catalogue.replace(GAME, [source("def update(): pass", path="../outside.py")])
    with pytest.raises(SyntaxError):
        catalogue.replace(GAME, [source("def broken(")])
    assert list(catalogue.rows) == [key]


def test_corrupt_incompatible_or_unwritable_cache_is_a_cold_fallback(tmp_path, monkeypatch):
    (tmp_path / "catalogue.json").write_text('{"payload":null}', encoding="utf-8")
    cache = profiling.MetadataCache(tmp_path)
    assert cache.failure and not cache.entries
    catalogue = profiling.Catalogue("process-1", cache=cache)
    catalogue.replace(GAME, [source("def update(): pass")])
    assert len(rows(catalogue)) == 1
    monkeypatch.setattr(profiling.MetadataCache, "BACKEND", "another-runtime")
    assert not profiling.MetadataCache(tmp_path).entries
    bad = profiling.MetadataCache(tmp_path / "absent")
    catalogue = profiling.Catalogue("process-2", cache=bad)
    catalogue.replace(GAME, [source("def update(): pass")])
    assert bad.failure and len(rows(catalogue)) == 1


def test_cache_refuses_linked_target_and_host_filenames_cannot_escape(tmp_path):
    target = tmp_path / "outside.json"
    target.write_text("untouched", encoding="utf-8")
    folder = tmp_path / "cache"
    folder.mkdir()
    (folder / "catalogue.json").hardlink_to(target)
    cache = profiling.MetadataCache(folder)
    assert cache.failure
    catalogue = profiling.Catalogue("process-1", cache=cache)
    catalogue.replace(GAME, [source("def update(): pass")])
    assert target.read_text() == "untouched"
    with pytest.raises(ValueError):
        profiling.files.write(-1, b"data", filename="../outside.json")


def test_cache_budget_applies_even_when_owner_admission_repeatedly_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(profiling.MetadataCache, "MAX_BYTES", 700)
    cache = profiling.MetadataCache(tmp_path)
    catalogue = profiling.Catalogue("process-1", cache=cache, limits=profiling.Limits(functions=1))
    for index in range(12):
        with pytest.raises(profiling.CatalogueError):
            catalogue.replace(GAME, [source(f"def one_{index}(): pass", module="first"),
                                     source(f"def two_{index}(): pass", module="second")])
    assert len(cache.entries) < 12
    # Valid discovery still caches/evicts within the same finite byte policy.
    for index in range(12):
        catalogue.replace(GAME, [source(f"def one_{index}(): pass")])
    cache.save()
    assert (tmp_path / "catalogue.json").stat().st_size <= 700
    assert len(cache.entries) < 12


def test_live_generation_exhaustion_is_atomic_and_null_tokens_cannot_mark_ready():
    catalogue = profiling.Catalogue("process-1")
    (key,) = catalogue.replace(GAME, [source("def update(): pass")])
    with pytest.raises(ValueError):
        catalogue.bind(key, None, runtime_kind="game-python", metrics=["calls"])
    catalogue.generation = 2**64 - 1
    with pytest.raises(profiling.CatalogueError):
        catalogue.bind(key, object(), runtime_kind="game-python", metrics=["calls"])
    assert catalogue.rows[key]["availability"] == "not_loaded" and not catalogue.bindings
    with pytest.raises(profiling.CatalogueError):
        catalogue.replace(GAME, [source("def changed(): pass")])
    assert list(catalogue.rows) == [key]


def test_unicode_pages_use_utf8_bytes_and_explicit_source_limits():
    catalogue = profiling.Catalogue("process-1")
    unit = source("def café水(): pass", module="módulo", path="src/物理.py")
    catalogue.replace(GAME, [unit])
    record = rows(catalogue)[0]
    assert record["qualified_name"] == "café水" and record["source"] == "src/物理.py"
    limited = profiling.Catalogue("process-1", limits=profiling.Limits(source_bytes=4))
    with pytest.raises(profiling.CatalogueError):
        limited.replace(GAME, [unit])
    assert not limited.rows


def test_limits_reserve_space_for_errors_and_empty_success_envelopes():
    for bound in (1, 1023, 3001):
        with pytest.raises(ValueError):
            profiling.Limits(page_bytes=bound)
    catalogue = profiling.Catalogue("process-1", limits=profiling.Limits(page_bytes=1024))
    assert query(catalogue)["data"]["functions"] == []
    for text in ("{", "[]", '["' + "水"*1500 + '"]'):
        response = catalogue.query("diagnostics.one", text)
        assert not json.loads(response)["ok"] and len(response.encode("utf-8")) <= 1024


def test_identical_bundle_skips_discovery_and_persistence_preserving_cursor(tmp_path, monkeypatch):
    cache = profiling.MetadataCache(tmp_path)
    catalogue = profiling.Catalogue("process-1", cache=cache, limits=profiling.Limits(page_bytes=1200))
    units = [source("def one(): pass\ndef two(): pass\ndef three(): pass")]
    keys = catalogue.replace(GAME, units)
    token = query(catalogue)["data"]["next_cursor"]
    monkeypatch.setattr(cache, "discover", lambda *a: pytest.fail("Unchanged bundle rediscovered"))
    monkeypatch.setattr(cache, "save", lambda: pytest.fail("Unchanged bundle persisted again"))
    assert catalogue.replace(GAME, units) == keys
    assert query(catalogue, cursor=token)["ok"]
