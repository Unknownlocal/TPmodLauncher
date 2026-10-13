# SPDX-License-Identifier: MPL-2.0
"""Compare complete runtime profiles and owned-manager startup, without starting the game."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def files(directory):
    """Return regular release files; exclude compiled Python and linked local state."""
    result = []
    for path in directory.rglob("*"):
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
            result.append(path)
    return sorted(result)


def profile(name, selected, destination):
    """Record every included file and measure a ZIP with the same settings for both profiles."""
    archive = destination / f"{name}.zip"
    with zipfile.ZipFile(
        archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as bundle:
        for path in selected:
            bundle.write(path, path.relative_to(ROOT))
    rows = []
    for path in selected:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )
    return {
        "installed_bytes": sum(item["bytes"] for item in rows),
        "zip_bytes": archive.stat().st_size,
        "files": rows,
    }


def main():
    """Measure selected release files and repeated fresh client processes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=5, help="fresh launches for each backend"
    )
    args = parser.parse_args()
    if not 1 <= args.runs <= 20:
        parser.error("--runs must be between 1 and 20")
    if os.name != "nt":
        parser.error(
            "The existing Python binding and embedded game adapter baseline is Windows-only"
        )
    destination = ROOT / ".cache/core-worker/measurements"
    destination.mkdir(parents=True, exist_ok=True)
    # Use the same verified generation as the current launcher, not other cached vendor trees.
    sys.path.insert(0, str(ROOT))
    from launcher.bundled_dependencies import verify_bundle
    from launcher.native_worker import verify_worker

    vendor = verify_bundle(ROOT)
    worker = verify_worker(ROOT)
    runtime = ROOT / "libs/python-3.13.16-embed-amd64"
    common = (
        files(runtime)
        + files(ROOT / "launcher")
        + files(ROOT / "hook_manager")
        + files(ROOT / "native/core_worker")
        + files(ROOT / "notices")
        + [
            ROOT / "LICENCE",
            ROOT / "ATTRIBUTION.txt",
            ROOT / "README.md",
            ROOT / "pyproject.toml",
        ]
    )
    results = {"kind": "stock-devkit baseline", "profiles": {}}
    results["profiles"]["python"] = profile(
        "python", common + files(vendor) + [ROOT / "bundled_packages.toml"], destination
    )
    results["profiles"]["native"] = profile(
        "native", common + files(worker.parent), destination
    )
    # A distinct executable path gives the controller an exact owned target identity.
    target_runtime = destination / "owned target"
    if not target_runtime.exists():
        shutil.copytree(runtime, target_runtime)
    probe = ROOT / "tests/native_worker/fixtures/readiness_probe.py"
    results["startup"] = {}
    for backend in ("python", "native"):
        runs = []
        for _ in range(args.runs):
            started = time.monotonic()
            completed = subprocess.run(
                [
                    str(runtime / "python.exe"),
                    "-B",
                    str(probe),
                    str(ROOT),
                    backend,
                    str(target_runtime / "python.exe"),
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stdout + completed.stderr)
            record = json.loads(completed.stdout)
            record["process_total_seconds"] = time.monotonic() - started
            runs.append(record)
        results["startup"][backend] = {
            "runs": runs,
            "median": {
                key: statistics.median(row[key] for row in runs) for key in runs[0]
            },
        }
    (destination / "results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "profiles": {
                    name: {key: value for key, value in row.items() if key != "files"}
                    for name, row in results["profiles"].items()
                },
                "startup": results["startup"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
