# SPDX-License-Identifier: MPL-2.0
"""Developer-only Python component build using the pinned componentize-py tool."""

import argparse
from importlib.metadata import version
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from launcher.bundled_dependencies import tree_paths
from launcher.mod_contract import Manifest


def build(folder, output):
    """Snapshot src into disposable staging; publish a new component without replacing files."""
    manifest = Manifest.read(folder)
    if manifest.runtime != "python" or version("componentize-py") != "0.25.1":
        raise ValueError("Python builds require componentize-py 0.25.1")
    output = Path(output).absolute()
    if output.suffix != ".wasm":
        raise ValueError("Expected a .wasm output path")
    if output.exists():
        raise FileExistsError(
            "Choose a new output path; existing components are preserved"
        )
    source = manifest.root / "src"
    if not source.is_dir() or source.is_symlink() or source.is_junction():
        raise ValueError("Expected an ordinary src folder")
    # Preflight links before copying; only Python source enters this initial build profile.
    files = [path for path in tree_paths(source) if path.is_file()]
    if (
        not files
        or any(path.suffix != ".py" for path in files)
        or sum(path.stat().st_size for path in files) > 1024 * 1024
    ):
        raise ValueError("Expected at most 1 MiB of Python source files")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mod-build-") as temporary:
        stage = Path(temporary)
        staged_source = stage / "source"
        staged_source.mkdir()
        for path in files:
            target = staged_source / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        # Manifest validation restricts api to supported versions; every contract has the same layout.
        wit = (
            Path(__file__).resolve().parents[1]
            / "hook_manager/api"
            / manifest.api
            / "mod.wit"
        )
        shutil.copyfile(wit, stage / "mod.wit")
        empty = stage / "empty-environment"
        (empty / "lib/site-packages").mkdir(parents=True)
        environment = os.environ.copy()
        # The upstream builder consults VIRTUAL_ENV explicitly; give it no installed packages to copy.
        environment["VIRTUAL_ENV"] = str(empty)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "from componentize_py import script; script()",
                "-d",
                str(stage / "mod.wit"),
                "-w",
                "code-mod",
                *(
                    ["--features", ",".join(sorted(manifest.capabilities))]
                    if manifest.capabilities
                    else []
                ),
                "componentize",
                "--stub-wasi",
                "-p",
                str(staged_source),
                "mod",
                "-o",
                str(stage / "mod.wasm"),
            ],
            cwd=stage,
            env=environment,
            check=True,
            timeout=120,
        )
        # Exclusive creation preserves another build's output; normal output-folder permissions apply.
        with output.open("xb") as target, (stage / "mod.wasm").open("rb") as binary:
            shutil.copyfileobj(binary, target)
    return output


def main():
    """Build an explicitly named author folder; no discovery or end-user installation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "folder", type=Path, help="mod folder containing mod.toml and src/mod.py"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="new .wasm output path"
    )
    arguments = parser.parse_args()
    print(build(arguments.folder, arguments.output))


if __name__ == "__main__":
    main()
