# SPDX-License-Identifier: MPL-2.0
"""Combine same-run Windows acceptance coverage without executing its contents."""

import argparse
from contextlib import closing
from pathlib import Path
import shutil
import sys

from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException


# Acceptance has three required fragments. The callable-native name also avoids
# colliding with the portable callable job's test-data-windows-callables artifact.
SUITES = ("embedded", "native", "callables-native")


def _clear_report(output_dir: Path) -> None:
    directory = output_dir / "windows"
    resolved = directory.resolve()
    if directory.is_symlink() or directory.is_junction() or resolved.parent != output_dir.resolve():
        raise OSError(f"unsafe report directory: {directory}")
    if directory.exists():
        shutil.rmtree(resolved)


def combine(input_dir: Path, output_dir: Path) -> list[str]:
    """Write one Windows report only when all acceptance fragments are valid.

    Portable component workflows generate their own reports in each test job.
    Run from the tested checkout so Windows relative paths resolve on Linux.
    Raw fragments remain available after successful or failed combination.
    """
    destination = output_dir / "windows"
    try:
        _clear_report(output_dir)
    except OSError as error:
        return [f"windows: cannot clear previous report: {error}"]
    fragments = [input_dir / f"test-data-windows-{suite}" / ".coverage" for suite in SUITES]
    missing = [str(fragment) for fragment in fragments if not fragment.is_file()]
    if missing:
        return [f"windows: missing coverage fragments: {', '.join(missing)}"]
    try:
        # combine() can warn and skip corrupt files. Validate every required input.
        for fragment in fragments:
            with closing(CoverageData(basename=str(fragment))) as data:
                data.read()
        destination.mkdir(parents=True)
        coverage = Coverage(data_file=str(destination / ".coverage"))
        coverage.combine(data_paths=[str(path) for path in fragments], strict=True, keep=True)
        coverage.save()
        coverage.xml_report(outfile=str(destination / "coverage.xml"))
        coverage.html_report(directory=str(destination / "htmlcov"))
    except (CoverageException, OSError) as error:
        errors = [f"windows: cannot combine coverage: {error}"]
        try:
            _clear_report(output_dir)
        except OSError as cleanup_error:
            errors.append(f"windows: cannot clear failed report: {cleanup_error}")
        return errors
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="downloaded test-data artifacts")
    parser.add_argument("--output", type=Path, default=Path("coverage-reports"))
    options = parser.parse_args(argv)
    errors = combine(options.input, options.output)
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
