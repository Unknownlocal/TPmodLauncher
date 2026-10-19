# SPDX-License-Identifier: MPL-2.0
"""Developer-only runner builder; player imports never load this toolchain.

Build only the fixed trusted guest, retain its source/notices and publish a fresh
bundle with its integrity receipt last. Incomplete output cannot pass verification.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .bundle import (
    ABI_VERSION, COMPONENTIZE_VERSION, EXPORTS, FILES, MANIFEST, NOTICE_FILES, SOURCES,
    RunnerBundle, BundleError, _checked, _read, MAX_COMPONENT_BYTES,
)
from .contract import HOST_OPERATIONS


def check_interface(data):
    """Validate the private ABI without instantiating or executing the runner."""
    from wasmtime import Config, Engine
    from wasmtime.component import Component, ComponentInstanceType, FuncType, String, U32

    def signature(function):
        kinds = {String: "string", U32: "u32"}
        if type(function) is not FuncType or any(type(value) not in kinds for _, value in function.params):
            raise BundleError("Unexpected runner function type")
        if function.result is not None and type(function.result) is not String:
            raise BundleError("Unexpected runner result type")
        return tuple(kinds[type(value)] for _, value in function.params), "string" if function.result is not None else None

    with Config() as config:
        config.wasm_component_model = True
        config.cranelift_opt_level = "none"
        with Engine(config) as engine, Component(engine, data) as component, component.type as interface:
            expected = {name: (spec.parameters, spec.result) for name, spec in HOST_OPERATIONS.items()}
            imports = {name: signature(item.ty) for name, item in interface.imports(engine).items()}
            public = interface.exports(engine)
            # The pinned toolchain adds one private binding-metadata instance.
            # It is not callable through this ABI; no other roots may be hidden.
            metadata = public.pop("exports", None)
            if metadata is None or type(metadata.ty) is not ComponentInstanceType:
                raise BundleError("Missing private runner binding metadata")
            exports = {name: signature(item.ty) for name, item in public.items()}
            if imports != expected or exports != EXPORTS:
                raise BundleError("Runner imports/exports differ from its declared ABI")


def build(output):
    """Build one runner from trusted source into a new directory."""
    if importlib.metadata.version("componentize-py") != COMPONENTIZE_VERSION:
        raise BundleError(f"Runner builds require componentize-py {COMPONENTIZE_VERSION}")
    output = _checked(output)
    if output.exists():
        raise FileExistsError("Choose a fresh runner output directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    package = Path(__file__).resolve().parent
    root = package.parent
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".runner-build-") as temporary:
        stage = Path(temporary)
        source = stage / "source"
        source.mkdir()
        (stage / "notices").mkdir()
        (stage / "empty/lib/site-packages").mkdir(parents=True)
        for name in SOURCES:
            basename = Path(name).name
            origin = package / ABI_VERSION / basename if basename == "runner.wit" else package / basename
            (source / basename).write_bytes(_read(origin, 1024 * 1024))
        # Freeze only this fixed trusted inventory; no author input enters builds.
        build_source = stage / "input"
        build_source.mkdir()
        for name in SOURCES:
            basename = Path(name).name
            if basename != "runner.wit":
                destination = "runner_guest.py" if basename == "guest.py" else basename
                shutil.copyfile(source / basename, build_source / destination)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["VIRTUAL_ENV"] = str(stage / "empty")
        subprocess.run([
            sys.executable, "-I", "-c", "from componentize_py import script; script()",
            "-d", str(source / "runner.wit"), "-w", "code-runner", "--world-module", "runner_bindings",
            "componentize", "--stub-wasi", "-p", str(build_source), "runner_guest",
            "-o", str(stage / "runner.wasm"),
        ], cwd=stage, env=environment, check=True, timeout=120)
        check_interface(_read(stage / "runner.wasm", MAX_COMPONENT_BYTES))
        for path in NOTICE_FILES:
            origin = root / "LICENCE" if path.endswith("LICENCE.Project.txt") else root / "notices/mod-runtime" / Path(path).name
            (stage / path).write_bytes(_read(origin, 2 * 1024 * 1024))
        records = {}
        for name in sorted(FILES):
            data = _read(stage / name, MAX_COMPONENT_BYTES)
            records[name] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        receipt = json.dumps(dict(format=1, abi=ABI_VERSION, componentize_py=COMPONENTIZE_VERSION, files=records), indent=2).encode() + b"\n"
        # Reserve late and exclusively. Existing output is never replaced, even if
        # another builder created it while compilation was running. Manifest last.
        output.mkdir()
        for name in sorted(FILES):
            target = output / name
            target.parent.mkdir(exist_ok=True)
            with target.open("xb") as destination:
                destination.write(_read(stage / name, MAX_COMPONENT_BYTES))
        with (output / MANIFEST).open("xb") as destination:
            destination.write(receipt)
    return RunnerBundle(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path, help="fresh runner-bundle directory")
    action.add_argument("--verify", type=Path, help="verify an existing bundle and its ABI")
    args = parser.parse_args(argv)
    bundle = build(args.output) if args.output else RunnerBundle(args.verify)
    if args.verify:
        check_interface(bundle.component())
    print(f"Runner bundle verified: {bundle.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
