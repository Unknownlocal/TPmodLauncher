# SPDX-License-Identifier: MPL-2.0
"""Create temporary projects, test wheels and copies of bundled Python and Frida files."""

from textwrap import dedent
import base64
import csv
import hashlib
import io
import os
from pathlib import Path
import shutil
import zipfile

import pytest

from launcher import bundled_dependencies as dependencies
from launcher import prepare_release as release


@pytest.fixture
def project_factory(tmp_path):
    """Return a function that creates a temporary project and its demo wheel."""
    counter = 0

    def build(*, metadata=None):
        nonlocal counter
        counter += 1
        root = tmp_path / f"project {counter}"
        directory = root / ".cache/wheels"
        directory.mkdir(parents=True)
        info = "demo-1.0.dist-info"
        files = {
            "demo/__init__.py": b"VALUE = 42\n",
            info
            + "/METADATA": (
                metadata
                or "Metadata-Version: 2.1\nName: demo\nVersion: 1.0\nRequires-Python: >=3.13\n"
            ).encode(),
            info
            + "/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            info
            + "/licenses/LICENSE": b"Synthetic test fixture, not third-party code.\n",
        }
        record = io.StringIO(newline="")
        writer = csv.writer(record)
        for name, data in files.items():
            digest = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .rstrip(b"=")
                .decode()
            )
            writer.writerow([name, "sha256=" + digest, len(data)])
        writer.writerow([info + "/RECORD", "", ""])
        files[info + "/RECORD"] = record.getvalue().encode()
        filename = "demo-1.0-py3-none-any.whl"
        wheel = directory / filename
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        settings = "\n".join(
            [
                "[tool.modlauncher.runtime]",
                'version = "3.13.16"',
                'directory = "libs/python-3.13.16-embed-amd64"',
                "[tool.modlauncher.build]",
                'pip_version = "26.2.1"',
                "",
            ]
        )
        (root / "pyproject.toml").write_text(settings, encoding="utf-8")
        lock = dedent(
            f"""\
            lock-version = "1.0"
            created-by = "pytest"
            requires-python = "==3.13.*"
            environments = ["sys_platform == 'win32' and platform_machine == 'AMD64'"]
            [[packages]]
            name = "demo"
            version = "1.0"
            [[packages.wheels]]
            name = "{filename}"
            path = ".cache/wheels/{filename}"
            hashes = {{sha256 = "{hashlib.sha256(wheel.read_bytes()).hexdigest()}"}}
            """
        )
        (root / "pylock.toml").write_text(lock, encoding="utf-8")
        return root, wheel

    return build


@pytest.fixture
def bundled_project(project_factory):
    """Create a test package and matching manifest; return the project and package folders."""
    root, _ = project_factory()
    runtime = release.read_settings(root)["runtime"]
    packages = release.read_lock(root, runtime)
    # A small source-only tree suffices; actual wheel installation is tested separately.
    directory = root / "libs/vendor/fixture"
    (directory / "demo").mkdir(parents=True)
    (directory / "demo/__init__.py").write_bytes(b"VALUE = 42\n")
    (directory / "COPYING").write_bytes(b"Synthetic licence fixture.\n")
    (root / "bundled_packages.toml").write_bytes(
        release.manifest_bytes(
            runtime, packages, dependencies.file_inventory(directory)
        )
    )
    published = root / release.package_directory(dependencies.file_inventory(directory))
    directory.rename(published)
    # This pytest-owned fixture is now a release, without developer build inputs.
    shutil.rmtree(root / ".cache")
    (root / "pylock.toml").unlink()
    (root / "pyproject.toml").unlink()
    return root, published


@pytest.fixture(scope="module")
def embedded_project(tmp_path_factory):
    """Return the folder of a temporary copy of bundled Python, Frida and launcher files."""
    source = Path(__file__).resolve().parents[1]
    runtime = source / "libs/python-3.13.16-embed-amd64"
    try:
        directory = dependencies.verify_bundle(source)
    except dependencies.DependencyError:
        directory = None
    if os.name != "nt" or not (runtime / "python.exe").exists() or directory is None:
        if os.environ.get("TPM_REQUIRE_EMBEDDED_TESTS") == "1":
            pytest.fail(
                "CI requires the prepared Windows runtime and package bundle; run release preparation first."
            )
        pytest.skip(
            "Prepare the bundled Windows runtime and packages to run embedded tests."
        )
    root = (
        tmp_path_factory.mktemp("relocated-manager-with-spaces") / "manager with spaces"
    )
    shutil.copytree(runtime, root / "libs/python-3.13.16-embed-amd64")
    shutil.copytree(directory, root / directory.relative_to(source))
    shutil.copytree(
        source / "launcher",
        root / "launcher",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        source / "hook_manager",
        root / "hook_manager",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copy2(source / "bundled_packages.toml", root / "bundled_packages.toml")
    shutil.copy2(source / "pyproject.toml", root / "pyproject.toml")
    worker = source / "libs/core_worker"
    if worker.exists():
        shutil.copytree(worker, root / "libs/core_worker")
    return root
