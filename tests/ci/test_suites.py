# SPDX-License-Identifier: MPL-2.0
"""Check portable CI ownership and selection without executing other suites."""

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "ci_tests", Path(__file__).resolve().parents[2] / ".github/ci_tests.py"
)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


@pytest.mark.parametrize(
    ("path", "suite"),
    [
        ("tests/launcher/test_new_feature.py", "launcher"),
        ("tests/hook_manager/test_menu.py", "hook_manager"),
        ("tests/hook_manager/career/test_preload.py", "career_preload"),
        ("tests/hook_manager/game_adapters/regulations/test_native_policy.py", "native_regulation_policy"),
        ("tests/hook_manager/game_adapters/projects/test_technical_profile.py", "project_context"),
        ("tests/callables/test_validation.py", "callables"),
        ("tests/mod_runtime/test_source.py", "mod_runtime"),
        ("tests/mod_runtime/loading/test_shared_sources.py", "mod_runtime_loading"),
        ("tests/ci/test_suites.py", "ci"),
    ],
)
def test_portable_modules_have_one_owner(tmp_path, path, suite):
    assert ci.suite_for(tmp_path / path, tmp_path) == suite


def test_unassigned_module_fails(tmp_path):
    with pytest.raises(pytest.UsageError, match="exactly one CI suite"):
        ci.suite_for(tmp_path / "tests/new_area/test_feature.py", tmp_path)


def test_overlapping_ownership_fails(tmp_path, monkeypatch):
    duplicate = "tests/mod_runtime/test_source.py"
    monkeypatch.setitem(ci.SUITES, "launcher", "tests/")
    with pytest.raises(pytest.UsageError, match="exactly one CI suite"):
        ci.suite_for(tmp_path / duplicate, tmp_path)


@pytest.mark.parametrize("suite", [*ci.SUITES, "check"])
def test_selection_keeps_partition_and_reports_deselections(tmp_path, suite):
    original = [
        SimpleNamespace(path=tmp_path / prefix / "test_fixture.py")
        for prefix in ci.SUITES.values()
    ]
    deselections = []
    config = SimpleNamespace(
        pluginmanager=SimpleNamespace(getplugin=lambda _: None),
        hook=SimpleNamespace(pytest_deselected=lambda items: deselections.extend(items)),
    )
    items = original.copy()
    ci.SuiteSelection(suite, tmp_path).pytest_collection_modifyitems(config, items)
    if suite == "check":
        assert items == original
        assert deselections == []
    else:
        assert len(items) == 1
        assert ci.suite_for(items[0].path, tmp_path) == suite
        assert [item for item in original if item not in items] == deselections


def test_empty_suite_fails(tmp_path):
    config = SimpleNamespace(pluginmanager=SimpleNamespace(getplugin=lambda _: None))
    with pytest.raises(pytest.UsageError, match="is empty"):
        ci.SuiteSelection("mod_runtime", tmp_path).pytest_collection_modifyitems(config, [])


def test_main_always_applies_portable_markers_and_check_only_collects(monkeypatch):
    calls = []
    monkeypatch.setattr(ci.pytest, "main", lambda args, plugins: calls.append((args, plugins)) or 0)
    assert ci.main(["--suite", "check", "-q"]) == 0
    args, plugins = calls.pop()
    assert args == [str(ci.ROOT / "tests"), "-q", "--collect-only", "-m", ci.PORTABLE]
    assert plugins[0].suite == "check"


def test_callable_coverage_is_separate_from_parent_hook_manager(monkeypatch):
    calls = []
    monkeypatch.setattr(ci.pytest, "main", lambda args, plugins: calls.append(args) or 0)
    assert ci.main(["--suite", "callables", "--coverage", "-p", "pytest_cov"]) == 0
    assert "--cov=hook_manager.callables" in calls[0]
    assert "--cov=hook_manager" not in calls[0]
    assert ci.main(["--suite", "hook_manager", "--coverage", "-p", "pytest_cov"]) == 0
    assert "--cov=hook_manager" in calls[1]
    config_file = next(arg.removeprefix("--cov-config=") for arg in calls[1] if arg.startswith("--cov-config="))
    from coverage import Coverage
    config = Coverage(config_file=config_file)
    assert config.get_option("run:omit") == ["*/hook_manager/callables/*",
                                            "*/hook_manager/career_preload.py",
                                            "*/hook_manager/game_adapters/regulations/native_policy.py",
                                            "*/hook_manager/game_adapters/regulations/native_categories.py",
                                            "*/hook_manager/game_adapters/regulations/native_scripts.py",
                                            "*/hook_manager/game_adapters/regulations/native_defaults.py",
                                            "*/hook_manager/game_adapters/projects/*"]
    assert config.get_option("run:branch") and config.get_option("run:relative_files")


def test_real_collection_filters_acceptance_before_assigning_suites(tmp_path):
    modules = {
        "tests/hook_manager/test_host.py": "def test_host(): pass\n",
        "tests/launcher/test_pack_mods.py": "def test_other_suite(): assert False\n",
        "tests/test_acceptance_only.py": (
            "import pytest\n@pytest.mark.native\ndef test_native(): assert False\n"
        ),
        "tests/test_legacy_only.py": (
            "import pytest\n@pytest.mark.legacy\ndef test_legacy(): assert False\n"
        ),
        "tests/test_release_only.py": (
            "import pytest\n@pytest.mark.release\ndef test_release(): assert False\n"
        ),
    }
    for name, source in modules.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    native: native acceptance\n    legacy: optional compatibility\n    release: optional release\n", encoding="utf-8"
    )
    script = """
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location('ci_tests', sys.argv[1])
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
root = pathlib.Path(sys.argv[2])
raise SystemExit(ci.pytest.main(
    [str(root / 'tests'), '-q', '-m', ci.PORTABLE],
    plugins=[ci.SuiteSelection('hook_manager', root)],
))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(spec.origin), str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 4 deselected" in result.stdout
