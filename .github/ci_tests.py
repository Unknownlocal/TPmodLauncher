# SPDX-License-Identifier: MPL-2.0
"""Run one portable CI suite and reject tests without an explicit suite owner."""

import argparse
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PORTABLE = "not embedded and not native and not frida_build and not legacy and not release"
SUITES = {
    "launcher": dict(path="tests/launcher/", sources=("launcher",), runner=True),
    "hook_manager": dict(path="tests/hook_manager/", sources=("hook_manager",)),
    "career_preload": dict(path="tests/hook_manager/career/", sources=("hook_manager/career_preload.py",), parent="hook_manager"),
    "game_adapters": dict(path="tests/hook_manager/game_adapters/", sources=("hook_manager/game_adapters",), parent="hook_manager"),
    "personnel": dict(path="tests/hook_manager/game_adapters/personnel/", sources=("hook_manager/game_adapters/personnel",), parent="game_adapters"),
    "regulations": dict(path="tests/hook_manager/game_adapters/regulations/", sources=("hook_manager/game_adapters/regulations",), parent="game_adapters"),
    "projects": dict(path="tests/hook_manager/game_adapters/projects/", sources=("hook_manager/game_adapters/projects",), parent="game_adapters"),
    "callables": dict(path="tests/callables/", sources=("hook_manager/callables",), parent="hook_manager", runner=True),
    "mod_runtime": dict(path="tests/mod_runtime/", sources=("mod_runtime",), runner=True),
    "mod_runtime_loading": dict(path="tests/mod_runtime/loading/", sources=("mod_runtime/_loading", "mod_runtime/shared_sources.py"), parent="mod_runtime", runner=True),
    "ci": dict(path="tests/ci/", sources=(".github",)),
    "integration_regulations": dict(path="tests/integration/regulations/", kind="integration", runner=True,
        sources=("launcher/regulations", "launcher/pack_mods.py", "mod_runtime/_regulations", "hook_manager/game_adapters/regulations")),
    "integration_source_loading": dict(path="tests/integration/source_loading/", kind="integration", runner=True,
        sources=("launcher/pack_mods.py", "launcher/shared_source_mod.py", "mod_runtime/shared_runtime.py", "mod_runtime/shared_sources.py", "mod_runtime/_loading")),
    "integration_session_lifetime": dict(path="tests/integration/session_lifetime/", kind="integration", runner=True,
        sources=("launcher/game_bridge.py", "launcher/process_lifecycle.py", "hook_manager/adapter_modules.py", "hook_manager/career_preload.py")),
}


def children(suite):
    """Use the same declared children for test selection and coverage exclusion."""
    return [row for row in SUITES.values() if row.get("parent") == suite]


def suite_for(path, root):
    relative = Path(path).relative_to(root).as_posix()
    matches = [suite for suite, row in SUITES.items()
               if relative.startswith(row["path"])
               and not relative.startswith(tuple(child["path"] for child in children(suite)))]
    if len(matches) != 1:
        raise pytest.UsageError(
            f"Portable test module {relative!r} must have exactly one CI suite; "
            f"found {matches!r}"
        )
    return matches[0]


class SuiteSelection:
    def __init__(self, suite, root=None):
        self.suite = suite
        self.root = ROOT if root is None else root

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
    parser.add_argument("--needs-runner", action="store_true", help="Print whether CI must download the shared runner")
    options, pytest_args = parser.parse_known_args(argv)
    if options.needs_runner:
        print(str(SUITES.get(options.suite, {}).get("runner", False)).lower())
        return 0
    path = ROOT / ("tests" if options.suite == "check" else SUITES[options.suite]["path"])
    # Child suites have their own jobs; skip their directories before importing tests.
    pytest_args.extend("--ignore=" + str(ROOT / child["path"]) for child in children(options.suite))
    if options.suite == "check":
        pytest_args.append("--collect-only")
    elif options.coverage:
        suite = SUITES[options.suite]
        # Discover files from directories so unimported declared files stay in reports.
        sources = sorted({((ROOT / source).parent if source.endswith(".py") else ROOT / source).as_posix()
                          for source in suite["sources"]})
        include = ["*/" + source + ("" if source.endswith(".py") else "/*") for source in suite["sources"]]
        # Integration measurements intentionally span components. Component
        # reports omit their children's sources, including unexecuted files.
        omit = ["*/" + source + ("" if source.endswith(".py") else "/*")
                for child in children(options.suite) for source in child["sources"]]
        config = ROOT / ".state/ci-coverage" / (options.suite + ".toml")
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("[tool.coverage.run]\nbranch = true\nrelative_files = true\n"
                          + "source = " + json.dumps(sources) + "\nomit = " + json.dumps(omit)
                          + "\n[tool.coverage.report]\ninclude = " + json.dumps(include) + "\n", encoding="utf-8")
        pytest_args.extend(["--cov", f"--cov-config={config}"])
    return pytest.main(
        [str(path), *pytest_args, "-m", PORTABLE],
        plugins=[SuiteSelection(options.suite)],
    )


if __name__ == "__main__":
    raise SystemExit(main())
