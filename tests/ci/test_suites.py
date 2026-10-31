# SPDX-License-Identifier: MPL-2.0
"""Check portable CI ownership and selection without executing other suites."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


spec = importlib.util.spec_from_file_location(
    "ci_tests", Path(__file__).resolve().parents[2] / ".github/ci_tests.py"
)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_overlapping_ownership_fails(tmp_path, monkeypatch):
    duplicate = "tests/mod_runtime/test_source.py"
    monkeypatch.setitem(ci.SUITES["launcher"], "path", "tests/")
    with pytest.raises(pytest.UsageError, match="exactly one CI suite"):
        ci.suite_for(tmp_path / duplicate, tmp_path)


@pytest.mark.parametrize("suite", ["game_adapters", "callables", "integration_regulations", "check"])
def test_real_selection_filters_acceptance_and_separates_coverage(tmp_path, suite):
    # Execute the CLI with a small owned tree: argument inspection alone cannot
    # prove that markers, collect-only mode or coverage exclusions take effect.
    modules = {
        "hook_manager/__init__.py": "",
        "hook_manager/parent.py": "def value(): return 'parent'\n",
        "hook_manager/callables/__init__.py": "",
        "hook_manager/callables/child.py": "def value(): return 'child'\n",
        "hook_manager/callables/unimported.py": "raise AssertionError('Coverage imported an unexecuted file')\n",
        "tests/hook_manager/test_host.py": "def test_host(): assert False\n",
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
        "tests/hook_manager/game_adapters/regulations/test_policy.py": "def test_policy(): assert False\n",
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
    selected = {
        "game_adapters": "tests/hook_manager/game_adapters/test_support.py",
        "callables": "tests/callables/test_callable.py",
        "integration_regulations": "tests/integration/regulations/test_integrated_policy.py",
    }.get(suite)
    for name, source in modules.items():
        if selected and name.startswith("tests/"):
            if name == selected:
                source += "\nimport pytest\n@pytest.mark.native\ndef test_acceptance_only(): assert False\n"
            else:
                source = "raise AssertionError('Unselected test module was imported')\n"
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
if sys.argv[2] == 'callables':
    ci.SUITES['callables']['sources'] = ('hook_manager/callables/child.py', 'hook_manager/callables/unimported.py')
args = ['--suite', sys.argv[2], '-q']
if sys.argv[2] != 'check':
    args += ['--coverage', '-p', 'pytest_cov', '--cov-report=xml:coverage.xml']
raise SystemExit(ci.main(args))
"""
    def run():
        return subprocess.run(
            [sys.executable, "-B", "-c", script, str(spec.origin), suite],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
            # The fixture's coverage must not overwrite or join the parent CI run.
            env={**os.environ, "COVERAGE_FILE": str(tmp_path / ".coverage")},
        )
    result = run()
    assert result.returncode == 0, result.stdout + result.stderr
    if suite == "check":
        assert "collected" in result.stdout and "passed" not in result.stdout
        assert all(name in result.stdout for name in ("test_host", "test_callable", "test_child", "test_other_suite"))
        assert not (tmp_path / "coverage.xml").exists()
        (tmp_path / "tests/test_unassigned.py").write_text("def test_unassigned(): pass\n", encoding="utf-8")
        result = run()
        assert result.returncode != 0 and "must have exactly one CI suite" in result.stderr
    else:
        assert "1 passed, 1 deselected" in result.stdout
        from xml.etree import ElementTree
        classes = list(ElementTree.parse(tmp_path / "coverage.xml").iter("class"))
        files = {Path(row.attrib["filename"]).name for row in classes} - {"__init__.py"}
        # Each report contains only its area's source, including unexecuted files.
        expected = {"game_adapters": {"common.py"}, "callables": {"child.py", "unimported.py"},
                    "integration_regulations": {"policy.py"}}
        assert files == expected[suite]
        if suite == "callables":
            assert next(row for row in classes if row.attrib["filename"].endswith("unimported.py")).attrib["line-rate"] == "0"
        if suite == "game_adapters":
            # A suite containing only acceptance tests must fail after marker filtering.
            (tmp_path / selected).write_text(
                "import pytest\n@pytest.mark.native\ndef test_acceptance_only(): assert False\n",
                encoding="utf-8",
            )
            result = run()
            assert result.returncode != 0 and "is empty" in result.stderr
