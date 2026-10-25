# SPDX-License-Identifier: MPL-2.0
"""Run one portable CI suite and reject tests without an explicit suite owner."""

import argparse
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PORTABLE = "not embedded and not native and not frida_build and not legacy and not release"
SUITES = {
    "launcher": "tests/launcher/",
    "hook_manager": "tests/hook_manager/",
    "career_discovery": "tests/hook_manager/career/",
    "native_regulation_policy": "tests/hook_manager/game_adapters/regulations/",
    # This package has its own test owner even though its source is under hook_manager.
    "callables": "tests/callables/",
    "mod_runtime": "tests/mod_runtime/",
    "mod_runtime_loading": "tests/mod_runtime/loading/",
    "ci": "tests/ci/",
}
# Parent suites exclude tests that run in their child workflows.
EXCLUDES = {"mod_runtime": ("tests/mod_runtime/loading/",),
            "hook_manager": ("tests/hook_manager/career/", "tests/hook_manager/game_adapters/"),}


def suite_for(path, root):
    relative = Path(path).relative_to(root).as_posix()
    matches = [suite for suite, prefix in SUITES.items()
               if relative.startswith(prefix)
               and not relative.startswith(EXCLUDES.get(suite, ()))]
    if len(matches) != 1:
        raise pytest.UsageError(
            f"Portable test module {relative!r} must have exactly one CI suite; "
            f"found {matches!r}"
        )
    return matches[0]


class SuiteSelection:
    def __init__(self, suite, root=ROOT):
        self.suite = suite
        self.root = root

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config, items):
        # Pytest's marker filtering runs first, so acceptance-only modules need
        # no portable owner and do not enter the partition counts.
        owners = [(item, suite_for(item.path, self.root)) for item in items]
        counts = {suite: 0 for suite in SUITES}
        for _, suite in owners:
            counts[suite] += 1
        reporter = config.pluginmanager.getplugin("terminalreporter")
        if reporter is not None:
            reporter.write_line(
                "Portable suites: " + ", ".join(f"{suite}={count}" for suite, count in counts.items())
            )
        if self.suite == "check":
            return
        selected = [item for item, suite in owners if suite == self.suite]
        deselected = [item for item, suite in owners if suite != self.suite]
        if not selected:
            raise pytest.UsageError(f"Portable CI suite {self.suite!r} is empty")
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=[*SUITES, "check"], required=True)
    parser.add_argument("--coverage", action="store_true", help="Measure only this suite's production area")
    options, pytest_args = parser.parse_known_args(argv)
    if options.suite == "check":
        pytest_args.append("--collect-only")
    elif options.coverage:
        # Test-area names and import paths differ for the private package. Keep
        # its measurements in its own artifact instead of the parent's report.
        source = {"callables": "hook_manager.callables", "ci": ".github",
                  "mod_runtime_loading": "mod_runtime",
                  "career_discovery": "hook_manager.career_discovery",
                  "native_regulation_policy": "hook_manager.game_adapters.regulations.native_policy"}.get(options.suite, options.suite)
        pytest_args.append(f"--cov={source}")
        if options.suite == "career_discovery":
            pytest_args.append("--cov=hook_manager.career_entry")
        if options.suite == "hook_manager":
            # Measure the directory, including unimported top-level files, while
            # assigning the private subpackage to its separate coverage report.
            pytest_args.append(f"--cov-config={ROOT / '.github/coverage-hook-manager.toml'}")
    return pytest.main(
        [str(ROOT / "tests"), *pytest_args, "-m", PORTABLE],
        plugins=[SuiteSelection(options.suite)],
    )


if __name__ == "__main__":
    raise SystemExit(main())
