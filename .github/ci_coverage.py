# SPDX-License-Identifier: MPL-2.0
"""Combine same-platform CI coverage fragments without executing their contents."""

import argparse
from contextlib import closing
from pathlib import Path
import shutil
import sys

from coverage import Coverage, CoverageData
from coverage.exceptions import CoverageException


SUITES = {
    "windows": ("host", "runner", "api", "pack", "embedded", "native"),
    "linux": ("host", "runner", "api", "pack"),
    "macos-x86_64": ("host", "runner", "api", "pack"),
    "macos-arm64": ("host", "runner", "api", "pack"),
}


def _clear_report(output_dir: Path, platform: str) -> None:
    directory = output_dir / platform
    resolved = directory.resolve()
    if directory.is_symlink() or directory.is_junction() or resolved.parent != output_dir.resolve():
        raise OSError(f"unsafe report directory: {directory}")
    if directory.exists():
        shutil.rmtree(resolved)


def combine(input_dir: Path, output_dir: Path) -> list[str]:
    """Write XML/HTML for complete platforms, returning errors for incomplete ones.

    Run from the tested source checkout so relative source paths, including
    Windows paths downloaded onto Linux, resolve through coverage's path mapping.
    Raw fragments are retained for inspection even when collection failed.
    """
    errors = []
    for platform, suites in SUITES.items():
        destination = output_dir / platform
        try:
            _clear_report(output_dir, platform)
        except OSError as error:
            errors.append(f"{platform}: cannot clear previous report: {error}")
            continue
        fragments = [
            input_dir / f"test-data-{platform}-{suite}" / ".coverage"
            for suite in suites
        ]
        missing = [str(fragment) for fragment in fragments if not fragment.is_file()]
        if missing:
            errors.append(f"{platform}: missing coverage fragments: {', '.join(missing)}")
            continue
        try:
            # combine() can warn and skip corrupt files. Validate each input first
            # so a report cannot quietly omit one of the required test jobs.
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
            try:
                _clear_report(output_dir, platform)
            except OSError as cleanup_error:
                errors.append(f"{platform}: cannot clear failed report: {cleanup_error}")
            errors.append(f"{platform}: cannot combine coverage: {error}")
    return errors


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
