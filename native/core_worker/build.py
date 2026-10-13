# SPDX-License-Identifier: MPL-2.0
"""Build a worker from a hash-checked official devkit; end users do not run this script."""

import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import shlex
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent


def sha256(path):
    """Hash a file without reading its whole contents into memory."""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def host_target():
    """Choose the release target matching the compiler host."""
    system = {"win32": "windows", "darwin": "macos", "linux": "linux"}[sys.platform]
    cpu = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}[
        platform.machine().lower()
    ]
    return f"{system}-{cpu}"


def cmake_command():
    """Find CMake on PATH or in an installed Windows Visual Studio toolchain."""
    command = shutil.which("cmake")
    if command is not None:
        return command
    if os.name == "nt":
        vswhere = (
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
            / "Microsoft Visual Studio/Installer/vswhere.exe"
        )
        installation = subprocess.check_output(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            text=True,
        ).strip()
        command = (
            Path(installation)
            / "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe"
        )
        if installation and command.is_file():
            return str(command)
    raise RuntimeError(
        "Install CMake and a native C compiler before building the worker"
    )


def sdk_link_dependencies(sdk):
    """Read Unix system link dependencies from the SDK's own generated compile example."""
    if os.name == "nt":
        return (
            [],
            [],
        )  # Windows system libraries are declared by frida-core.h and CMake.
    text = (sdk / "frida-core-example.c").read_text(encoding="utf-8")
    command = next(
        line.strip().removeprefix("* ")
        for line in text.splitlines()
        if "-lfrida-core" in line
    )
    words = shlex.split(command)
    libraries = [
        word[2:] for word in words if word.startswith("-l") and word != "-lfrida-core"
    ]
    frameworks = [words[i + 1] for i, word in enumerate(words) if word == "-framework"]
    return libraries, frameworks


def build():
    """Verify the SDK, compile locally and publish the worker plus its file manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="require the official archive already in the project cache",
    )
    args = parser.parse_args()
    pins = tomllib.loads((SOURCE / "devkits.toml").read_text(encoding="utf-8"))
    target = host_target()
    if target not in pins["targets"]:
        raise RuntimeError(f"No reviewed devkit pin for {target}")
    version = pins["version"]
    filename = f"frida-core-devkit-{version}-{target}.tar.xz"
    cache = ROOT / ".cache/core-worker"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / filename
    url = f"https://github.com/frida/frida/releases/download/{version}/{filename}"
    if not archive.exists():
        if args.offline:
            raise RuntimeError(f"Official archive is not cached: {filename}")
        # Downloads stay in local build state; a failed hash never reaches the compiler.
        urllib.request.urlretrieve(url, archive)
    expected = pins["targets"][target]["sha256"]
    if sha256(archive) != expected:
        raise RuntimeError("Frida devkit archive failed its SHA256 check")
    sdk = cache / "sdk" / target
    sdk.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        bundle.extractall(sdk, filter="data")
    cmake = cmake_command()
    libraries, frameworks = sdk_link_dependencies(sdk)
    directory = cache / "build" / target
    configure = [
        cmake,
        "-S",
        str(SOURCE),
        "-B",
        str(directory),
        f"-DFRIDA_DEVKIT={sdk}",
        f"-DFRIDA_SYSTEM_LIBRARIES={';'.join(libraries)}",
        f"-DFRIDA_SYSTEM_FRAMEWORKS={';'.join(frameworks)}",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    if os.name == "nt":
        configure += ["-A", "x64"]
    subprocess.run(configure, check=True)
    subprocess.run(
        [cmake, "--build", str(directory), "--config", "Release", "--parallel", "2"],
        check=True,
    )
    destination = ROOT / "libs/core_worker" / target
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            cmake,
            "--install",
            str(directory),
            "--config",
            "Release",
            "--prefix",
            str(destination),
        ],
        check=True,
    )
    executable = destination / ("core-worker.exe" if os.name == "nt" else "core-worker")
    # Match the ordinary stripped release size on Unix; Windows link removes unused sections.
    if os.name != "nt":
        subprocess.run(["strip", str(executable)], check=True)
    manifest = (
        f'frida = "{version}"\ntarget = "{target}"\n'
        f'sha256 = "{sha256(executable)}"\n'
        f'sdk_sha256 = "{expected}"\nsdk_url = "{url}"\n'
    )
    (destination / "worker.toml").write_text(manifest, encoding="utf-8")
    print(f"Prepared {executable}: {executable.stat().st_size} bytes")


if __name__ == "__main__":
    build()
