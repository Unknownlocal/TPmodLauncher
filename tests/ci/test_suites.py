# SPDX-License-Identifier: MPL-2.0
"""Check portable CI ownership and selection without executing other suites."""

import importlib.util
import os
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


def test_unassigned_module_fails(tmp_path):
    with pytest.raises(pytest.UsageError, match="exactly one CI suite"):
        ci.suite_for(tmp_path / "tests/new_area/test_feature.py", tmp_path)


def test_overlapping_ownership_fails(tmp_path, monkeypatch):
    duplicate = "tests/mod_runtime/test_source.py"
    monkeypatch.setitem(ci.SUITES["launcher"], "path", "tests/")
    with pytest.raises(pytest.UsageError, match="exactly one CI suite"):
        ci.suite_for(tmp_path / duplicate, tmp_path)


@pytest.mark.parametrize("suite", ["hook_manager", "mod_runtime", "check"])
def test_selection_keeps_partition_and_reports_deselections(tmp_path, suite):
    original = [
        SimpleNamespace(path=tmp_path / row["path"] / "test_fixture.py")
        for row in ci.SUITES.values()
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


@pytest.mark.parametrize("suite", ["hook_manager", "game_adapters", "callables", "integration_regulations", "check"])
def test_real_selection_filters_acceptance_and_separates_coverage(tmp_path, suite):
    # Execute the CLI with a small owned tree: argument inspection alone cannot
    # prove that markers, collect-only mode or coverage exclusions take effect.
    modules = {
        "hook_manager/__init__.py": "",
        "hook_manager/parent.py": "def value(): return 'parent'\n",
        "hook_manager/callables/__init__.py": "",
        "hook_manager/callables/child.py": "def value(): return 'child'\n",
        "tests/hook_manager/test_host.py": (
            "from hook_manager import parent\nfrom hook_manager.callables import child\n"
            "def test_host(): assert (parent.value(), child.value()) == ('parent', 'child')\n"
        ),
        "tests/callables/test_callable.py": (
            "from hook_manager import parent\nfrom hook_manager.callables import child\n"
            "def test_callable(): assert (parent.value(), child.value()) == ('parent', 'child')\n"
        ),
        "hook_manager/game_adapters/__init__.py": "",
        "hook_manager/game_adapters/common.py": "def value(): return 'adapter'\n",
        "hook_manager/game_adapters/regulations/__init__.py": "",
        "hook_manager/game_adapters/regulations/policy.py": "def value(): return 'policy'\n",
        "tests/hook_manager/game_adapters/test_support.py": (
            "from hook_manager.game_adapters import common\nfrom hook_manager.game_adapters.regulations import policy\n"
            "def test_support(): assert (common.value(), policy.value()) == ('adapter', 'policy')\n"
        ),
        "tests/hook_manager/game_adapters/regulations/test_policy.py": (
            "from hook_manager.game_adapters import common\nfrom hook_manager.game_adapters.regulations import policy\n"
            "def test_policy(): assert (common.value(), policy.value()) == ('adapter', 'policy')\n"
        ),
        "tests/integration/regulations/test_integrated_policy.py": (
            "from hook_manager.game_adapters import common\nfrom hook_manager.game_adapters.regulations import policy\n"
            "def test_integration(): assert (common.value(), policy.value()) == ('adapter', 'policy')\n"
        ),
        "tests/hook_manager/career/test_preload.py": "def test_child(): assert False\n",
        "tests/launcher/test_pack_mods.py": "def test_other_suite(): assert False\n",
        **{f"tests/test_{marker}_only.py": (
            f"import pytest\n@pytest.mark.{marker}\ndef test_{marker}(): assert False\n"
        ) for marker in ("native", "legacy", "release")},
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
sys.path.insert(0, str(pathlib.Path.cwd()))
spec = importlib.util.spec_from_file_location('ci_tests', sys.argv[1])
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
ci.ROOT = pathlib.Path.cwd()
args = ['--suite', sys.argv[2], '-q']
if sys.argv[2] != 'check':
    args += ['--coverage', '-p', 'pytest_cov', '--cov-report=xml:coverage.xml']
raise SystemExit(ci.main(args))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(spec.origin), suite],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        # The fixture's coverage must not overwrite or join the parent CI run.
        env={**os.environ, "COVERAGE_FILE": str(tmp_path / ".coverage")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if suite == "check":
        assert "collected" in result.stdout and "passed" not in result.stdout
        assert all(name in result.stdout for name in ("test_host", "test_callable", "test_child", "test_other_suite"))
        assert not (tmp_path / "coverage.xml").exists()
    else:
        assert "1 passed, 9 deselected" in result.stdout
        from xml.etree import ElementTree
        files = {row.attrib["filename"] for row in ElementTree.parse(tmp_path / "coverage.xml").iter("class")}
        # Both areas ran. Only the selected area's source belongs in its report.
        assert any(name.endswith("parent.py") for name in files) == (suite == "hook_manager")
        assert any(name.endswith("child.py") for name in files) == (suite == "callables")
        assert any(name.endswith("common.py") for name in files) == (suite == "game_adapters")
        assert any(name.endswith("policy.py") for name in files) == (suite in ("regulations", "integration_regulations"))
