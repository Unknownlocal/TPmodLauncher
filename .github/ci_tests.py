# SPDX-License-Identifier: MPL-2.0
"""Run one portable CI suite and reject tests without an explicit suite owner."""

import argparse
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PORTABLE = "not embedded and not native and not frida_build"
SUITES = {
    "host": {
        "tests/launcher/test_bundled_dependencies.py",
        "tests/launcher/test_compilation_cache.py",
        "tests/launcher/test_sandbox.py",
        "tests/launcher/test_release_preparation.py",
        "tests/launcher/test_frida_build.py",
        "tests/launcher/test_frida_sources.py",
        "tests/launcher/test_frida_macos_packaging.py",
    },
    "runner": {
        "tests/mod_runtime/test_bundle.py",
        "tests/mod_runtime/test_source.py",
        "tests/mod_runtime/test_runtime.py",
        "tests/mod_runtime/test_preparation.py",
    },
    "api": {
        "tests/mod_runtime/test_host.py",
        "tests/mod_runtime/test_mod_adapter.py",
        "tests/launcher/test_mod_contract.py",
    },
    "pack": {
        "tests/launcher/test_pack_mods.py",
        "tests/launcher/test_source_pack.py",
        "tests/launcher/test_menu_mod.py",
        "tests/launcher/test_process_lifecycle.py",
        "tests/launcher/test_runner_release.py",
    },
}


def suite_for(path, root):
    relative = Path(path).relative_to(root).as_posix()
    matches = [suite for suite, files in SUITES.items() if relative in files]
    if relative.startswith(("tests/hook_manager/", "tests/ci/")):
        matches.append("host")
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
    options, pytest_args = parser.parse_known_args(argv)
    if options.suite == "check":
        pytest_args.append("--collect-only")
    return pytest.main(
        [str(ROOT / "tests"), *pytest_args, "-m", PORTABLE],
        plugins=[SuiteSelection(options.suite)],
    )


if __name__ == "__main__":
    raise SystemExit(main())
