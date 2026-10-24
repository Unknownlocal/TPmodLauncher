# SPDX-License-Identifier: MPL-2.0
"""Run clean timing and separately instrumented owned-process cases, outside CI."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from fixtures import CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--mode", choices=("legacy", "resident"), required=True)
    parser.add_argument("--native-python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--cases", nargs="+", choices=tuple(CASES), default=tuple(CASES))
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for name in args.cases:
        for diagnostics in (False, True):
            output = args.output / (name + ("-diagnostics" if diagnostics else "-timing") + ".json")
            command = [sys.executable, "-B", str(Path(__file__).with_name("worker.py")),
                       "--root", str(args.root.resolve()), "--bundle", str(args.bundle.resolve()),
                       "--mode", args.mode, "--case", name, "--native-python", str(args.native_python.resolve()),
                       "--samples", str(1 if diagnostics else args.samples), "--output", str(output.resolve())]
            if diagnostics:
                command.append("--diagnostics")
            subprocess.run(command, check=True, timeout=240)
            reports.append(json.loads(output.read_text()))
    (args.output / "report.json").write_text(json.dumps(reports, indent=2) + "\n")


if __name__ == "__main__":
    main()
