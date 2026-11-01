# SPDX-License-Identifier: MPL-2.0
"""Selected-pack source admission, real runner execution and native readiness."""

from contextlib import closing
import faulthandler
import json
import runpy
import shutil
from pathlib import Path
import threading
import time
import sys
from types import SimpleNamespace

import pytest

from hook_manager.menu import MenuAdapter
from hook_manager.settings import SettingsAdapter
from hook_manager.themes import THEME_ROLES
from launcher.pack_mods import PackMods
from launcher.compiled_mod import CompiledMod
from launcher.sandbox import SandboxError
from mod_runtime import preparation
from mod_runtime.source import SourceSnapshot


# Cold compilation and source loading need headroom on
# shared CI hosts; these behaviour checks are not a startup benchmark.
SOURCE_PACK_LOAD_TIMEOUT = 120


@pytest.fixture
def pack(tmp_path):
    folder = tmp_path / "data/owned pack"
    folder.mkdir(parents=True)
    return tmp_path, folder, dict(state="ready", series="formula", id=folder.name, root=str(folder.resolve()))


def finish(scope, messages=()):
    deadline = time.monotonic() + SOURCE_PACK_LOAD_TIMEOUT
    while scope.loading is not None:
        if time.monotonic() >= deadline:
            faulthandler.dump_traceback()
            pytest.fail(
                f"Source pack did not finish loading within {SOURCE_PACK_LOAD_TIMEOUT}s; "
                f"loaded={list(scope.mods)!r}; failed={scope.failed}; total={scope.total}; "
                f"messages={messages!r}"
            )
        scope.poll()
        if scope.loading is not None:
            threading.Event().wait(0.002)


def records(messages, event):
    # Activity context and complete trace text can follow the event's JSON data.
    return [json.JSONDecoder().raw_decode(line.split(": ", 1)[1])[0]
            for line in messages if line.startswith(f"CODE MOD {event}:")]


def test_sources_and_explicit_wasm_share_order_progress_and_cleanup(pack, source_mod, code_mod, runner_bundle):
    game, folder, record = pack
    code = '''import json
import wit_world
DATA = wit_world.import_mod('owned.c', 'shared')
class WitWorld:
 def activate(self):
  DATA.values.append(OWNER)
  wit_world.report('source ready')
 def on_event(self, name, payload):
  return json.dumps(DATA.values) if name == 'peer' else payload
'''
    source_mod(folder, "z-folder", mod_id="owned.a", code="OWNER='owned.a'\n" + code)
    code_mod(folder, "a-folder", mod_id="owned.b")
    peer = source_mod(folder, "peer-folder", mod_id="owned.c", code="OWNER='owned.c'\n" + code.replace(
        "DATA = wit_world.import_mod('owned.c', 'shared')", "import shared as DATA"))
    (peer / "src/shared.py").write_text("values = []\n")
    messages, progress = [], []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle,
                          background=True, set_loading=progress.append)) as scope:
        scope.load(record)
        finish(scope)
        assert list(scope.mods) == ["owned.a", "owned.b", "owned.c"]
        assert scope.mods["owned.a"].sandbox is scope.mods["owned.c"].sandbox
        assert scope.mods["owned.a"].sandbox is not scope.mods["owned.b"].sandbox
        assert scope.mods["owned.a"].snapshot is scope.mods["owned.c"].snapshot is None
        assert scope.mods["owned.a"].event("peer", None) == ["owned.a", "owned.c"]
        assert scope.mods["owned.c"].event("peer", None) == ["owned.a", "owned.c"]
        # Mods without regulation access must not read regulations.json.
        assert scope.prepare_regulations(scopes=set()) is None
        assert scope.mods["owned.a"].event("owned.event", {"ok": True}) == {"ok": True}
        assert scope.mods["owned.a"].sandbox._thread == threading.get_ident()
        assert scope.mods["owned.b"].event("owned.event", None) == {}
        assert progress[-1] == dict(state="complete", loaded=3, failed=0, total=3)
        assert [row["id"] for row in records(messages, "ACTIVE")] == ["owned.a", "owned.b", "owned.c"]
        assert records(messages, "REPORT") == [dict(id=owner, message="source ready") for owner in ("owned.a", "owned.c")]
        assert len(records(messages, "SOURCE GROUP TIMING")) == 1
        instances = list(scope.mods.values())
    assert all(mod.sandbox.closed for mod in instances)
    assert [item["id"] for item in records(messages, "CLOSED")] == ["owned.c", "owned.b", "owned.a"]


def test_regulation_permission_cannot_expand_other_api_profiles(pack, source_mod, code_mod, runner_bundle, monkeypatch):
    game, folder, record = pack
    for api in ("0.2.0", "0.3.0"):
        source_mod(folder, "old-" + api, api=api, capabilities=("regulations",))
    source_mod(folder, "host-target", api="0.4.0", capabilities=("call-native",))
    binary = code_mod(folder, "prebuilt")
    manifest = binary / "mod.toml"
    manifest.write_text(manifest.read_text().replace('runtime = "python"', 'runtime = "wasm"')
                        .replace('["diagnostics"]', '["regulations"]'), encoding="utf-8")
    # A rejected declaration never gains access to pack data or guest execution.
    (folder / "regulations.json").write_text("invalid JSON deliberately ignored", encoding="utf-8")
    def forbidden(*args):
        pytest.fail("Rejected permissions must not load source or native hooks")
    monkeypatch.setattr(SourceSnapshot, "read", forbidden)
    messages = []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle,
                          bridge=SimpleNamespace(request=forbidden))) as scope:
        scope.load(record)
        finish(scope)
        assert not scope.mods and scope.failed == 4
        assert len(records(messages, "REJECTED")) == 4


def test_duplicate_ids_reject_all_copies_before_any_source_runs(pack, source_mod, code_mod, runner_bundle, monkeypatch):
    game, folder, record = pack
    source_mod(folder, "copy-a", mod_id="owned.duplicate", code="while True: pass")
    code_mod(folder, "copy-b", mod_id="owned.duplicate")
    def forbidden(*args):
        pytest.fail("Duplicate source must not be read or executed")
    monkeypatch.setattr(SourceSnapshot, "read", forbidden)
    messages, progress = [], []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle, set_loading=progress.append)) as scope:
        scope.load(record)
        finish(scope)
        assert not scope.mods
        assert progress[-1] == dict(state="complete", loaded=0, failed=2, total=2)
        assert [item["detail"] for item in records(messages, "REJECTED")] == ["Duplicate mod ID"] * 2


def test_closing_session_keeps_a_queued_compilation_needed_by_another_session(pack, source_mod, runner_bundle, monkeypatch):
    game, folder, record = pack
    source_mod(folder, "owned.first")
    other_folder = game / "data/other pack"
    source_mod(other_folder, "owned.second")
    other_record = {**record, "id": other_folder.name, "root": str(other_folder.resolve())}
    entered, release = threading.Event(), threading.Event()
    requests = []
    original = CompiledMod.__init__

    def occupy():
        entered.set()
        assert release.wait(SOURCE_PACK_LOAD_TIMEOUT)

    def request(self, *args, **kwargs):
        if not requests:
            # Put a gate ahead of the first compilation, after its source read.
            kwargs["executor"].submit(occupy)
        original(self, *args, **kwargs)
        requests.append(self)

    monkeypatch.setattr(CompiledMod, "__init__", request)
    try:
        with closing(PackMods(game, lambda _: None, runner_bundle=runner_bundle)) as first, closing(
            PackMods(game, lambda _: None, runner_bundle=runner_bundle)
        ) as second:
            for number, scope, pack_record in ((1, first, record), (2, second, other_record)):
                scope.load(pack_record)
                end = time.monotonic() + SOURCE_PACK_LOAD_TIMEOUT
                while len(requests) < number:
                    assert time.monotonic() < end
                    scope.poll()
                    threading.Event().wait(.002)
            assert entered.wait(5)
            future = requests[1]._entry.future
            assert future is requests[0]._entry.future and not future.running()
            first.close()
            assert not future.cancelled()
            release.set()
            finish(second)
            assert list(second.mods) == ["owned.second"] and not second.failed
            assert second.mods["owned.second"].event("owned.event", {"ok": True}) == {"ok": True}
    finally:
        release.set()


@pytest.mark.parametrize("failure", ["syntax", "missing"])
def test_bad_source_rejects_fixed_group_and_preserves_prebuilt_neighbor(pack, source_mod, code_mod, runner_bundle, failure):
    game, folder, record = pack
    bad = source_mod(folder, "a-bad", mod_id="owned.bad")
    if failure == "syntax":
        (bad / "src/mod.py").write_text("def invalid(:")
    else:
        (bad / "src/mod.py").unlink()
    source_mod(folder, "b-good", mod_id="owned.good")
    code_mod(folder, "c-wasm", mod_id="owned.wasm")
    messages, progress = [], []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle, set_loading=progress.append)) as scope:
        scope.load(record)
        finish(scope)
        assert list(scope.mods) == ["owned.wasm"]
        assert progress[-1] == dict(state="complete", loaded=1, failed=2, total=3)
        assert len(records(messages, "REJECTED")) == 2
        assert records(messages, "SOURCE GROUP FAILED")[0]["owners"] == ["owned.bad", "owned.good"]
        assert not records(messages, "REPORT")
        assert scope.mods["owned.wasm"].event("probe", None) == {}


def test_manifest_changes_after_admission_are_rejected_before_compile(pack, source_mod, runner_bundle, monkeypatch):
    game, folder, record = pack
    mod = source_mod(folder, "owned.source")
    original = SourceSnapshot.read
    def changed(path):
        manifest = Path(path) / "mod.toml"
        content = manifest.read_text()
        content = content.replace('["diagnostics"]', "[]")
        manifest.write_text(content)
        return original(path)
    def forbidden(*args, **kwargs):
        pytest.fail("Changed metadata must be rejected before native compilation")
    monkeypatch.setattr(SourceSnapshot, "read", changed)
    monkeypatch.setattr(preparation, "CompiledMod", forbidden)
    messages = []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle)) as scope:
        scope.load(record)
        finish(scope)
        assert not scope.mods and scope.failed == 1
        rejected = records(messages, "REJECTED")[0]
        assert rejected["id"] == "owned.source" and rejected["folder"] == mod.name
        assert "changed after admission" in rejected["detail"]
        assert records(messages, "SOURCE GROUP TIMING")[0]["phase_seconds"]["source_prepare"] >= 0


def test_invalid_prerequisite_is_rejected_before_joining_the_shared_source_group(pack, source_mod, runner_bundle):
    game, folder, record = pack
    bad = source_mod(folder, "bad", capabilities=("main-menu",), code="raise AssertionError('must not import')")
    manifest = bad / "mod.toml"
    manifest.write_text(manifest.read_text() + '\n[prerequisites]\n"menu:tools" = ["career.ready"]\n')
    source_mod(folder, "healthy")
    messages = []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle)) as scope:
        scope.load(record)
        finish(scope)
        assert set(scope.mods) == {"healthy"} and scope.failed == 1
        assert "Unknown prerequisite" in records(messages, "REJECTED")[0]["detail"]
        assert scope.mods["healthy"].event("probe", {"ok": True}) == {"ok": True}


@pytest.mark.parametrize("activation_failure", [False, True])
def test_source_ui_settings_api_owns_storage_registration_and_failure_cleanup(
    pack, source_mod, runner_bundle, activation_failure,
):
    game, folder, record = pack
    definition = {"schema": 1, "label": "Theme", "fields": [
        {"id": "colour_11", "label": "Custom Theme Colour 11", "color": "#3070B9"},
        {"id": "colour_52", "label": "Custom Theme Colour 52", "color": "#000000A0"},
    ]}
    code = f'''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self):
        self.saved = wit_world.register_settings({json.dumps(json.dumps(definition))})
        {"raise ValueError('owned activation failure')" if activation_failure else "pass"}
    def on_event(self, name, payload):
        if name == "save": return wit_world.save_settings(payload)
        return self.saved
'''
    root = source_mod(folder, "owned.theme", code=code, capabilities=("ui-settings",), api="0.3.0")
    source_mod(folder, "owned.other")
    messages = []
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle)) as scope:
        scope.load(record)
        finish(scope)
        if activation_failure:
            assert not scope.mods and scope.failed == 2
            assert scope.settings.records == []
            assert not (root / "settings.json").exists()
            return
        assert list(scope.mods) == ["owned.other", "owned.theme"]
        mod = scope.mods["owned.theme"]
        assert mod.event("load", None)["values"]["colour_11"] == "#3070B9"
        assert scope.settings.records[0]["owner"] == "owned.theme"
        assert not (root / "settings.json").exists()
        value = {"schema": 1, "values": {"colour_11": "#123456", "colour_52": "#00000080"}}
        assert mod.event("save", value) == value
        assert json.loads((root / "settings.json").read_text()) == value
        assert not (folder / "owned.other/settings.json").exists()
        # A malformed event closes all source peers, releases rows and keeps the last save.
        with pytest.raises(SandboxError, match="registered field IDs"):
            mod.event("save", {"schema": 1, "values": {"colour_11": "#FFFFFF"}})
        assert not scope.mods and scope.failed == 2
        assert scope.settings.records == []
        assert json.loads((root / "settings.json").read_text()) == value
    assert scope.settings.records == []


def test_theme_example_native_save_ack_retry_notification_and_cleanup(
    pack, source_mod, runner_bundle, native_settings, monkeypatch,
):
    game, folder, startup = pack
    theme = folder / "theme example"
    shutil.copytree(Path(__file__).resolve().parents[3] / "examples/theme_mod", theme)
    # Saved colours are loaded by field ID when the mod registers.
    palette = runpy.run_path(str(theme / "src/palette.py"))
    saved = {"schema": palette["PALETTE_SCHEMA"], "values": {
        f"colour_{n}": "#" + "".join(f"{channel:02X}" for channel in colour)
        for n, colour, _ in palette["COLOR_GROUPS"]
    }}
    saved["values"]["colour_18"] = "#123456"
    (theme / "settings.json").write_text(json.dumps(saved))
    previous_settings = (theme / "settings.json").read_bytes()
    source_mod(folder, "owned.other")
    adapter, messages, palettes = SettingsAdapter(), [], []
    native_originals = native_settings.ORIGINAL_METHODS.copy()
    with closing(adapter), closing(PackMods(
        game, messages.append, runner_bundle=runner_bundle,
        set_settings=lambda rows, result=None: {"settings": adapter.update(rows, result)},
        set_themes=lambda rows: palettes.append(rows),
    )) as scope:
        scope.load(startup)
        finish(scope)
        assert list(scope.mods) == ["example.theme", "owned.other"]
        fields = scope.settings.records[0]["fields"]
        assert len(fields) == 52
        assert [field["id"] for field in fields] == [f"colour_{n}" for n in range(1, 53)]
        assert ("code-mod:example.theme", "Theme") in native_settings.OPTION_TABS
        assert (theme / "settings.json").read_bytes() == previous_settings
        assert fields[17]["value"] == "#123456"
        assert palettes[-1][0]["colours"]["TEXT"] == palettes[-1][0]["colours"]["SELECTION_TEXT"] == "#123456"
        assert palettes[-1][0]["label"] == "Custom Theme"
        assert len(palettes[-1][0]["colours"]) == 59
        values = {field["id"]: field["value"] for field in fields}
        values["colour_1"] = "#ABCDEF"
        adapter.queue_save("example.theme", {"schema": 1, "values": values})
        request = adapter.status()
        notifications = []
        original_event = scope.mods["example.theme"].event
        def event(name, payload):
            notifications.append((name, payload))
            return original_event(name, payload)
        monkeypatch.setattr(scope.mods["example.theme"], "event", event)
        scope.settings_status(request)
        assert json.loads((theme / "settings.json").read_text())["values"] == values
        assert notifications == [("ui-settings.changed", {"schema": 1, "values": values})]
        assert palettes[-1][0]["colours"]["BACKGROUND"] == "#ABCDEF"
        assert palettes[-1][0]["colours"]["PRIMARY"] == palettes[-1][0]["colours"]["SELECTION_BORDER"]
        assert adapter.pending is None and adapter.messages["example.theme"] == "Settings saved."
        # Both stale generations and duplicate IDs in a current generation are ignored.
        scope.settings_status(request)
        scope.settings_status(adapter.status() | {"save": request["save"]})
        assert len(notifications) == 1
        previous = (theme / "settings.json").read_bytes()
        previous_palette = palettes[-1]
        from launcher import _settings_files
        original_write = _settings_files.write
        def fail(*args):
            raise OSError("owned disk failure")
        monkeypatch.setattr(_settings_files, "write", fail)
        values["colour_1"] = "#010203"
        adapter.queue_save("example.theme", {"schema": 1, "values": values})
        scope.settings_status(adapter.status())
        assert (theme / "settings.json").read_bytes() == previous
        assert adapter.pending is None and "Try again" in adapter.messages["example.theme"]
        assert len(notifications) == 1 and "example.theme" in scope.mods
        assert palettes[-1] == previous_palette
        monkeypatch.setattr(_settings_files, "write", original_write)
        adapter.queue_save("example.theme", {"schema": 1, "values": values})
        scope.settings_status(adapter.status())
        assert json.loads((theme / "settings.json").read_text())["values"] == values
        assert len(notifications) == 2
        with pytest.raises(SandboxError):
            scope.mods["example.theme"].event("invalid/event", None)
        assert not scope.mods
        assert adapter.records == []
        assert scope.themes.records == palettes[-1] == []
    assert native_settings.OptionsScreen.draw is native_originals["draw"]
    assert native_settings.OPTION_TABS == [("game", "Game"), ("graphics", "Graphics")]
    # A new mod group reads the saved palette during registration.
    with closing(PackMods(game, messages.append, runner_bundle=runner_bundle,
                          set_themes=lambda rows: palettes.append(rows))) as restarted:
        restarted.load(startup)
        finish(restarted)
        assert palettes[-1][0]["colours"]["BACKGROUND"] == "#010203"


@pytest.mark.parametrize("failure", ["themes", "settings"])
def test_deferred_ui_failure_revokes_all_shared_operation_owners(pack, source_mod, runner_bundle, failure):
    game, folder, startup = pack
    palette = {"label": "Palette Only", "colours": {
        role: "#00000080" if role == "OVERLAY" else "#112233" for role in sorted(THEME_ROLES)
    }}
    definition = {"schema": 1, "label": "Widget", "fields": [
        {"id": "accent", "label": "Accent", "color": "#112233"},
    ]}
    for owner, operation, data in (("owned.palette", "register_theme", palette),
                                   ("owned.settings", "register_settings", definition)):
        source_mod(folder, owner, capabilities=("ui-settings",), api="0.3.0", code=f'''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self):
        wit_world.{operation}({json.dumps(json.dumps(data))})
    def on_event(self, name, payload):
        return payload
''')
    registries = {"themes": [], "settings": []}
    def publish(area, rows):
        registries[area].append(rows)
        return {area: {"state": "pending" if rows else "idle", "generation": len(registries[area])}}
    messages = []
    with closing(PackMods(
        game, messages.append, runner_bundle=runner_bundle,
        set_themes=lambda rows: publish("themes", rows),
        set_settings=lambda rows, result=None: publish("settings", rows),
    )) as scope:
        scope.load(startup)
        finish(scope, messages)
        assert list(scope.mods) == ["owned.palette", "owned.settings"]
        assert all(mod.manifest.capabilities == frozenset({"ui-settings"}) for mod in scope.mods.values())
        handler = getattr(scope, f"{failure}_status")
        generation = getattr(scope, f"{failure}_generation")
        handler({"state": "failed", "generation": generation - 1})
        assert len(scope.mods) == 2
        handler({"state": "failed", "generation": generation})
        assert not scope.mods and scope.failed == 2
        assert scope.settings.records == scope.themes.records == []
        assert registries["settings"][-1] == registries["themes"][-1] == []


@pytest.mark.parametrize("availability", ["ready", "pending", "conflict"])
def test_source_menu_readiness_and_failure_rollback(
    pack, source_mod, runner_bundle, native_menu, monkeypatch, availability,
):
    game, folder, record = pack
    root = Path(__file__).resolve().parents[3]
    source_mod(folder, "owned.menu", code=(root / "examples/menu_mod/src/mod.py").read_text(),
               capabilities=("diagnostics", "main-menu"))
    source_mod(folder, "owned.diagnostics")
    hub, _ = native_menu
    native_rows, native_activate = hub.MENU_ITEMS.copy(), hub.HubScreen._activate
    if availability != "ready":
        monkeypatch.delitem(sys.modules, hub.__name__)
    messages = []
    with closing(MenuAdapter()) as adapter, closing(PackMods(
        game, messages.append, runner_bundle=runner_bundle,
        set_menu=lambda items: {"menu": adapter.update(items)}, set_loading=adapter.set_loading,
    )) as scope:
        scope.load(record)
        finish(scope, messages)
        if availability != "ready":
            assert len(scope.mods) == 2 and adapter.state == "pending"
            assert adapter.status()["loading"]["blocked"]
            if availability == "conflict":
                # A native row with the same label must survive rejected mod registration.
                hub.MENU_ITEMS.insert(0, "Mod Launcher")
                native_rows = hub.MENU_ITEMS.copy()
            monkeypatch.setitem(sys.modules, hub.__name__, hub)
            scope.menu_status(adapter.poll())
        assert not adapter.status()["loading"]["blocked"]
        assert list(scope.mods) == ([] if availability == "conflict" else ["owned.diagnostics", "owned.menu"])
        if availability != "conflict":
            assert adapter._notice() == "Mods loaded: 2" and "Mod Launcher" in hub.MENU_ITEMS
            screen = hub.HubScreen()
            screen._activate("Mod Launcher")
            assert screen.error_message == "The sandboxed UI mod is active"
            assert scope.mods["owned.diagnostics"].event("owned.event", {"ok": True}) == {"ok": True}
            with pytest.raises(SandboxError):
                scope.mods["owned.menu"].event("bad/event", None)
            assert "Mod Launcher" not in hub.MENU_ITEMS and not scope.mods
    assert hub.MENU_ITEMS == native_rows and hub.HubScreen._activate is native_activate


def test_import_time_ui_publishes_all_members_once_and_rolls_back_rejected_group(pack, source_mod, code_mod, runner_bundle):
    game, folder, record = pack
    for owner in ("owned.a", "owned.c"):
        source_mod(folder, owner, capabilities=("main-menu",), code=f'''import wit_world
wit_world.add_menu_button('open', '{owner}', 'Message')
class WitWorld:
 def activate(self): pass
 def on_event(self,n,p): return p
''')
    code_mod(folder, "owned.b")
    publications, progress = [], []
    def publish(rows):
        publications.append(rows)
        return {"menu": {"generation": len(publications), "state": "failed" if rows else "idle",
                         "detail": "Owned native rejection"}}
    with closing(PackMods(game, lambda _: None, runner_bundle=runner_bundle,
                          set_menu=publish, set_loading=progress.append)) as scope:
        scope.load(record)
        finish(scope)
        assert [[row["owner"] for row in rows] for rows in publications] == [["owned.a", "owned.c"], []]
        assert list(scope.mods) == ["owned.b"] and scope.failed == 2
        assert scope._source_runner.closed and scope.menu.buttons == []
        assert progress[-1] == dict(state="complete", loaded=1, failed=2, total=3)


@pytest.mark.parametrize("phase", ["manifest", "import", "activate"])
def test_mod_failure_logs_the_actual_owner_or_shared_preparation(pack, source_mod, code_mod, runner_bundle, phase):
    from launcher.diagnostics import reporter

    game, folder, record = pack
    code = ("raise RuntimeError('Owned import failure')\n" if phase == "import" else
            "import wit_world\nclass WitWorld(wit_world.WitWorld):\n def activate(self): raise RuntimeError('Owned activation failure')\n")
    failing = source_mod(folder, "a-failing", mod_id="owned.a", code=code)
    if phase == "manifest":
        (failing / "mod.toml").write_text("invalid = [", encoding="utf-8")
    source_mod(folder, "b-peer", mod_id="owned.b")
    code_mod(folder, "c-survivor", mod_id="owned.c")
    path = game / "diagnostics.log"
    with reporter(path) as logger, closing(PackMods(game, logger, runner_bundle=runner_bundle, background=True)) as scope:
        scope.load(record)
        finish(scope)
        assert list(scope.mods) == (["owned.b", "owned.c"] if phase == "manifest" else ["owned.c"])
        assert scope.failed == (1 if phase == "manifest" else 2)
    lines = path.read_text(encoding="utf-8").splitlines()
    failed = next(line for line in lines if ("CODE MOD REJECTED:" if phase == "manifest" else "CODE MOD SOURCE GROUP FAILED:") in line)
    context = json.loads(failed.partition(" activity=")[2])
    assert context["kind"] == "mod_loading"
    if phase == "manifest":
        assert context["folder"] == "a-failing" and "mod" not in context
        assert "Traceback (most recent call last)" in "\n".join(lines)
    elif phase == "import":
        assert context["owners"] == ["owned.a", "owned.b"] and "mod" not in context
    else:
        assert context["mod"] == "owned.a"
    survived = next(line for line in lines if "CODE MOD ACTIVE:" in line and "owned.c" in line)
    assert json.loads(survived.partition(" activity=")[2])["mod"] == "owned.c"
