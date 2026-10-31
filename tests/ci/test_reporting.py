# SPDX-License-Identifier: MPL-2.0
"""Run the trusted inline CI scripts with synthetic reports and job results."""

import json
from pathlib import Path
import runpy
import textwrap

import pytest


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github/workflows"


def run_workflow_script(filename):
    # Both workflows end with their only inline Python step. Read that trusted
    # source directly so these checks also exercise the code GitHub will run.
    workflow = WORKFLOWS / filename
    _, marker, source = workflow.read_text(encoding="utf-8").partition("        run: |\n")
    assert marker, f"No inline script in {workflow}"
    exec(compile(textwrap.dedent(source), str(workflow), "exec"), {})


@pytest.fixture
def reports(tmp_path, monkeypatch):
    suites = runpy.run_path(str(WORKFLOWS.parent / "ci_tests.py"))["SUITES"]
    names = [
        f"python-coverage-{suite}-{platform}"
        for suite in suites
        for platform in ("windows", "linux")
    ] + ["python-coverage-acceptance-windows"]
    for covered, name in enumerate(names, start=1):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "coverage.xml").write_text(
            f'<coverage lines-covered="{covered}" lines-valid="{len(names)}" '
            'branches-covered="1" branches-valid="2"/>', encoding="utf-8"
        )
    for name, value in {
        "REPORT_DIRECTORY": str(tmp_path),
        "TEST_RUN_URL": "https://github.com/example/repo/actions/runs/123",
        "TEST_RUN_SHA": "abc123",
        "TEST_RUN_CONCLUSION": "success",
        "TEST_RUN_WORKFLOW": "Test suite",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }.items():
        monkeypatch.setenv(name, value)
    return tmp_path, names


def test_reports_every_suite_and_platform_separately(reports):
    directory, names = reports
    run_workflow_script("coverage.yml")
    summary = (directory / "summary.md").read_text(encoding="utf-8")
    rows = [line for line in summary.splitlines() if "x64 |" in line]
    assert len(rows) == len(names)
    for covered, row in enumerate(rows, start=1):
        assert f"{covered / len(names):.2%} ({covered}/{len(names)})" in row
        assert "50.00% (1/2)" in row
    assert "| Launcher | Windows x64 |" in rows[0]
    assert "| Launcher | Linux x64 |" in rows[1]
    assert "| Private callable adapters | Windows x64 |" in summary
    assert "| Runtime acceptance | Windows x64 |" in rows[-1]
    assert "abc123" in summary
    assert "/actions/runs/123" in summary


def test_missing_suite_report_fails_instead_of_publishing_partial_success(reports):
    directory, _ = reports
    (directory / "python-coverage-regulations-linux/coverage.xml").unlink()
    with pytest.raises(SystemExit, match="Missing coverage report for Regulation adapters / Linux x64"):
        run_workflow_script("coverage.yml")
    summary = (directory / "summary.md").read_text(encoding="utf-8")
    assert "| Regulation adapters | Linux x64 | Report unavailable |" in summary


@pytest.mark.parametrize("xml", [
    "<coverage",
    "<unexpected/>",
    '<coverage lines-covered="11" lines-valid="10" branches-covered="1" branches-valid="2"/>',
    '<coverage lines-covered="1" lines-valid="10" branches-covered="-1" branches-valid="2"/>',
])
def test_invalid_coverage_fails_reporting(reports, xml):
    directory, _ = reports
    (directory / "python-coverage-acceptance-windows/coverage.xml").write_text(xml, encoding="utf-8")
    with pytest.raises(SystemExit, match="Invalid coverage report for Runtime acceptance / Windows x64"):
        run_workflow_script("coverage.yml")
    assert "Invalid report" in (directory / "summary.md").read_text(encoding="utf-8")


def test_empty_coverage_denominators_are_reported_without_division_by_zero(reports):
    directory, _ = reports
    (directory / "python-coverage-ci-linux/coverage.xml").write_text(
        '<coverage lines-covered="0" lines-valid="0" branches-covered="0" branches-valid="0"/>',
        encoding="utf-8",
    )
    run_workflow_script("coverage.yml")
    assert "| CI helpers | Linux x64 | N/A (0/0) | N/A (0/0) |" in (
        directory / "summary.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("suite,result", [
    ("callables", "success"),
    ("callables", "failure"),
    ("runner", "cancelled"),
    ("windows-runtime", "skipped"),
])
def test_parent_completion_requires_preparation_and_every_suite_to_succeed(monkeypatch, suite, result):
    results = {name: {"result": "success"} for name in (
        "dependencies", "runner", "launcher", "hook-manager", "callables", "mod-runtime", "ci", "integration", "windows-runtime"
    )}
    results[suite]["result"] = result
    monkeypatch.setenv("JOB_RESULTS", json.dumps(results))
    if result == "success":
        run_workflow_script("tests.yml")
    else:
        with pytest.raises(SystemExit, match=f"{suite}: {result}"):
            run_workflow_script("tests.yml")
