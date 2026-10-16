# SPDX-License-Identifier: MPL-2.0
"""Exercise CI report aggregation using real branch-coverage databases."""

import importlib.util
import gc
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import warnings

from coverage import Coverage, CoverageData
import pytest


@pytest.fixture
def helper():
    path = Path(__file__).resolve().parents[2] / ".github" / "ci_coverage.py"
    spec = importlib.util.spec_from_file_location("ci_coverage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fragments(tmp_path, monkeypatch, helper):
    monkeypatch.chdir(tmp_path)
    package = tmp_path / "launcher"
    package.mkdir()
    source = package / "example.py"
    source.write_text("def choose(flag):\n    if flag:\n        return 1\n    return 2\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.coverage.run]\nbranch = true\nsource = ["launcher"]\nrelative_files = true\n',
        encoding="utf-8",
    )
    input_dir = tmp_path / "inputs"
    for platform, suites in helper.SUITES.items():
        for index, suite in enumerate(suites):
            directory = input_dir / f"test-data-{platform}-{suite}"
            directory.mkdir(parents=True)
            measured = Coverage(data_file=str(directory / ".coverage"))
            measured.start()
            namespace = {}
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
            # Windows gets both branches across separate jobs. Other platforms
            # only get the true branch, proving platform data is not merged.
            namespace["choose"](platform != "windows" or index != 1)
            measured.stop()
            measured.save()
            if platform == "windows":
                # Real Windows coverage uses relative backslash paths. Rewrite
                # a measured fragment's path to exercise cross-OS combination.
                original = CoverageData(basename=str(directory / ".coverage"))
                original.read()
                arcs = {name.replace("/", "\\"): original.arcs(name) for name in original.measured_files()}
                original.erase()
                original.add_arcs(arcs)
                original.write()
    return input_dir, tmp_path / "reports"


def test_combines_disjoint_branches_without_merging_platforms(helper, fragments):
    input_dir, output = fragments
    assert helper.main(["--input", str(input_dir), "--output", str(output)]) == 0
    counts = {}
    for platform in helper.SUITES:
        report = ET.parse(output / platform / "coverage.xml").getroot()
        counts[platform] = (int(report.attrib["lines-covered"]), int(report.attrib["branches-covered"]))
        assert (output / platform / "htmlcov" / "index.html").is_file()
    assert counts["windows"] == (4, 2)
    assert counts["linux"] == counts["macos-x86_64"] == counts["macos-arm64"] == (3, 1)
    assert all(path.is_file() for path in input_dir.glob("*/.coverage"))


def test_missing_fragment_rejects_only_its_platform_and_clears_old_report(helper, fragments, capsys):
    input_dir, output = fragments
    assert helper.main(["--input", str(input_dir), "--output", str(output)]) == 0
    (input_dir / "test-data-windows-native" / ".coverage").unlink()
    assert helper.main(["--input", str(input_dir), "--output", str(output)]) == 1
    assert "windows: missing coverage fragments:" in capsys.readouterr().err
    assert not (output / "windows").exists()
    assert (output / "linux" / "coverage.xml").is_file()


def test_corrupt_fragment_does_not_produce_partial_platform_report(helper, fragments, capsys):
    input_dir, output = fragments
    (input_dir / "test-data-linux-runner" / ".coverage").write_bytes(b"broken sqlite")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        assert helper.main(["--input", str(input_dir), "--output", str(output)]) == 1
        gc.collect()
    assert not [warning for warning in caught if issubclass(warning.category, ResourceWarning)]
    assert "linux: cannot combine coverage:" in capsys.readouterr().err
    assert not (output / "linux").exists()
    assert (output / "windows" / "coverage.xml").is_file()


def test_missing_input_directory_reports_each_platform(helper, tmp_path, capsys):
    output = tmp_path / "reports"
    assert helper.main(["--input", str(tmp_path / "missing"), "--output", str(output)]) == 1
    errors = capsys.readouterr().err
    assert all(f"{platform}: missing coverage fragments:" in errors for platform in helper.SUITES)
    assert not output.exists()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks requires Windows developer mode")
def test_report_cleanup_rejects_links_to_outside_directories(helper, tmp_path, capsys):
    output = tmp_path / "reports"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    (output / "linux").symlink_to(outside, target_is_directory=True)
    assert helper.main(["--input", str(tmp_path / "missing"), "--output", str(output)]) == 1
    assert "linux: cannot clear previous report: unsafe report directory:" in capsys.readouterr().err
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert (output / "linux").is_symlink()
