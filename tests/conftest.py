# SPDX-License-Identifier: MPL-2.0
"""Create temporary projects, test wheels and copies of bundled runtime files."""

from textwrap import dedent
import base64
import csv
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import sys
import sysconfig
from types import ModuleType, SimpleNamespace
import zipfile

import pytest

from launcher import bundled_dependencies as dependencies
from launcher import prepare_release as release
from mod_runtime import RunnerBundle
from mod_runtime.bundle import DIRECTORY


@pytest.fixture(scope="session")
def runner_bundle(tmp_path_factory):
    """Use this workflow's verified artifact; local runs build once per session."""
    shared = os.environ.get("TPM_TEST_RUNNER_BUNDLE")
    if shared:
        bundle = RunnerBundle(shared)
        package = Path(__file__).resolve().parents[1] / "mod_runtime"
        from mod_runtime.bundle import ABI_VERSION, SOURCES
        for name in SOURCES:
            relative = Path(name).relative_to("source")
            current = package / ABI_VERSION / relative if relative == Path("runner.wit") else package / relative
            if bundle._file(name) != current.read_bytes():
                pytest.fail(f"Shared runner sources differ from the tested checkout: {name}")
        return bundle
    if os.environ.get("TPM_REQUIRE_SHARED_RUNNER") == "1":
        pytest.fail("CI requires TPM_TEST_RUNNER_BUNDLE; do not rebuild the runner in test jobs")
    from mod_runtime.build import build
    return build(tmp_path_factory.mktemp("runner-build") / "bundle")


@pytest.fixture(scope="session")
def python_compilation_cache(tmp_path_factory):
    """Reuse native code within this test process, never guest state or grants."""
    from launcher.compilation_cache import CompilationCache
    return CompilationCache(tmp_path_factory.mktemp("python-native-cache"))


@pytest.fixture(autouse=True)
def cached_python_components(request, monkeypatch):
    """Give Python integration tests the same cache used by normal launcher entry.

    Keep real compilation and the normal ready/pending code-sharing behaviour.
    Explicit cache instances (including cache-policy tests) retain their own setup.
    """
    if not {"runner_bundle", "built_python_mod", "built_menu_mod"}.intersection(request.fixturenames):
        return
    shared_cache = request.getfixturevalue("python_compilation_cache")
    from launcher.compiled_mod import CompiledMod
    original = CompiledMod.__init__
    def cached(instance, data, timings=None, *, cache=None, executor=None):
        return original(instance, data, timings,
                        cache=shared_cache if cache is None else cache, executor=executor)
    monkeypatch.setattr(CompiledMod, "__init__", cached)


@pytest.fixture
def disposable_source_compiler(monkeypatch):
    """Model compiler-result ownership in gated cancellation tests, without JIT work.

    These tests never admit the result to a Store. Real compilation and adoption
    are exercised separately by the preparation integration test.
    """
    from launcher import compilation_registry
    class DisposableCompilation:
        def __init__(self, *args, **kwargs):
            self.closed = False
            self.engine = self.component = None
            self.cache_status = None
        def close(self):
            self.closed = True
    monkeypatch.setattr(compilation_registry, "CompiledComponent", DisposableCompilation)


@pytest.fixture
def native_menu(monkeypatch):
    """Provide the small native menu interface used by owned adapter and mod tests."""
    hub = ModuleType("ui.screens.hub_screen")
    calls = []

    class HubScreen:
        def _item_enabled(self, item):
            return item != "Continue Season"

        def _disabled_item_message(self, item):
            return "native disabled message"

        def _activate(self, item):
            calls.append(item)
            return "native activation"

        def draw(self, surface):
            calls.append(("draw", surface))
            return "native draw"

    hub.HubScreen, hub.MENU_ITEMS = HubScreen, ["Career", "Load", "Exit"]
    hub.pygame = SimpleNamespace(time=SimpleNamespace(get_ticks=lambda: 10))
    monkeypatch.setitem(sys.modules, hub.__name__, hub)
    return hub, calls


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
        lock = dedent(f"""\
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
            """)
        (root / "pylock.toml").write_text(lock, encoding="utf-8")
        return root, wheel

    return build


@pytest.fixture
def native_settings(monkeypatch):
    """Provide an owned native options interface shared by adapter and loader tests."""
    import importlib.util
    name = "ui.screens.options_screen"
    path = Path(__file__).parent / "hook_manager/fixtures/native_settings_fixture.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, name, module)
    return module


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
def embedded_project(tmp_path_factory, request):
    """Return a temporary copy of bundled Python, dependencies and launcher files."""
    # An isolated prepared candidate lets the same acceptance tests exercise either binding.
    candidate = os.environ.get("TPM_TEST_RELEASE_ROOT")
    source = Path(candidate if candidate is not None else Path(__file__).resolve().parents[1]).resolve()
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
    shutil.copytree(source / "mod_runtime", root / "mod_runtime", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(source / "examples", root / "examples", ignore=shutil.ignore_patterns("__pycache__", "*.wasm"))
    shutil.copy2(source / "bundled_packages.toml", root / "bundled_packages.toml")
    # Explicit release acceptance must exercise that candidate's own artifact.
    # Checkout tests use freshly built/shared bytes without replacing installed libs.
    selected_runner = (RunnerBundle(source / DIRECTORY) if candidate is not None
                       else request.getfixturevalue("runner_bundle"))
    selected_runner.copy_to(root / DIRECTORY)
    return root


@pytest.fixture(scope="module")
def native_project(request, tmp_path_factory):
    """Provide a real Frida test bundle and CPython 3.13 for Windows or Linux helpers."""
    if os.name == "nt":
        root = request.getfixturevalue("embedded_project")
        client = root / "libs/python-3.13.16-embed-amd64/python.exe"
        return SimpleNamespace(root=root, client=client, target=client)
    output = os.environ.get("TPM_FRIDA_BUILD_OUTPUT")
    if (
        sys.platform != "linux"
        or sys.version_info[:2] != (3, 13)
        or not sysconfig.get_config_var("Py_ENABLE_SHARED")
        or not output
    ):
        if os.environ.get("TPM_REQUIRE_NATIVE_TESTS") == "1":
            pytest.fail(
                "Native tests require Windows's bundle or Linux shared CPython 3.13 and TPM_FRIDA_BUILD_OUTPUT"
            )
        pytest.skip(
            "Native bridge tests need Windows's bundle or Linux shared CPython 3.13 and a reduced build"
        )
    from packaging.pylock import Package
    from packaging.version import Version
    from launcher import build_frida

    recipe = build_frida.read_recipe()
    wheel = build_frida.verify_output(
        Path(output), recipe, build_frida.host_target(recipe)
    )
    root = tmp_path_factory.mktemp("native-manager-with-spaces") / "manager with spaces"
    package = root / "libs/vendor/stage"
    package.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as archive:
        # The build verifier has checked the wheel paths, bytes and native platform.
        archive.extractall(package)
    inventory = dependencies.file_inventory(package)
    runtime = {
        "version": platform.python_version(),
        "directory": "libs/python-native-test",
    }
    (root / "bundled_packages.toml").write_bytes(
        release.manifest_bytes(
            runtime,
            [
                (
                    Package(
                        name="frida",
                        version=Version(recipe["frida"]["package_version"]),
                    ),
                    None,
                )
            ],
            inventory,
        )
    )
    package.rename(root / release.package_directory(inventory))
    source = Path(__file__).resolve().parents[1]
    for name in ("launcher", "hook_manager", "mod_runtime"):
        shutil.copytree(
            source / name, root / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    return SimpleNamespace(
        root=root, client=Path(sys.executable), target=Path(sys._base_executable)
    )
