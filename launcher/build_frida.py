# SPDX-License-Identifier: MPL-2.0
"""Build and check reduced upstream Frida wheels; never install into the launcher."""

import argparse
from email.parser import Parser
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import uuid
import zipfile

from packaging.utils import parse_wheel_filename
from packaging.version import Version

# Reuse release download/path/archive checks instead of another installer.
_spec = importlib.util.spec_from_file_location(
    "_frida_release", Path(__file__).with_name("prepare_release.py")
)
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)
Error = release.dependencies.DependencyError
ROOT = Path(__file__).resolve().parent.parent
RECIPE = Path(__file__).with_name("frida_build.toml")


def read_recipe(path=RECIPE):
    """Read source, feature and archive pins; reject incomplete or unsafe settings."""
    recipe = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    frida = recipe["frida"]
    version = Version(frida["package_version"])
    if version.public != frida["version"] or version.local is None:
        raise Error("Our wheel needs a local version suffix matching the Frida version")
    if frida["repository"] != "https://github.com/frida/frida.git":
        raise Error("Expected the official Frida source repository")
    # Check paths before using them as Git working folders or filesystem names.
    submodules = recipe["submodules"]
    if not isinstance(submodules, list) or len(submodules) != len(set(submodules)):
        raise Error("Required submodules must be a list of distinct paths")
    for name in submodules:
        release.dependencies.safe_name(name)
    if not re.fullmatch("[0-9a-f]{40}", frida["revision"]):
        raise Error("The Frida revision must be a full Git commit ID")
    if not re.fullmatch("[0-9]{8}", frida["deps_version"]):
        raise Error("Invalid upstream dependency version")
    options = frida["options"]
    if not options or any(
        not re.fullmatch(r"-D[a-z0-9_:-]+=[a-z0-9_,]+", item) for item in options
    ):
        raise Error("Expected explicit Meson feature options")
    if len({item.split("=", 1)[0] for item in options}) != len(options):
        raise Error("Duplicate Meson feature option")
    for names in recipe["targets"].values():
        if not names or len(names) != len(set(names)):
            raise Error("Each target needs distinct pinned build archives")
        for name in names:
            if not re.fullmatch(r"(?:toolchain|sdk)-[a-z0-9_-]+", name):
                raise Error("Unsafe build archive name")
            if not re.fullmatch("[0-9a-f]{64}", recipe["archives"][name]):
                raise Error("Build archives need SHA-256 pins")
    return recipe


def recipe_digest(recipe):
    """Hash the settings independently of comments and Git's line-ending conversion."""
    return hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def host_target(recipe):
    """Return the native build target; reject OS/CPU combinations outside this recipe."""
    system = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system()
    )
    arch = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(platform.machine().lower())
    target = f"{system}-{arch}"
    if target not in recipe["targets"]:
        raise Error(f"Unsupported native build target: {target}")
    return target


def git(directory, *arguments):
    """Run Git with long paths enabled for this command only; return its text output."""
    try:
        return subprocess.run(
            ["git", "-c", "core.longpaths=true", "-C", str(directory), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        # Keep clone/pin failures understandable instead of hiding Git's reason.
        raise Error(f"Git failed: {error.stderr.strip()}") from error


def submodule_owner(name, names):
    """Find the parent repository of a nested submodule."""
    parents = [
        other for other in names if PurePosixPath(other) in PurePosixPath(name).parents
    ]
    return max(parents, key=len, default=".")


def submodule_revision(source, name, names):
    """Read the submodule commit stored in its parent's checked-out commit."""
    owner = submodule_owner(name, names)
    relative = (
        PurePosixPath(name).relative_to(owner).as_posix() if owner != "." else name
    )
    entry = git(source / owner, "ls-tree", "HEAD", "--", relative)
    fields = entry.partition("\t")[0].split()
    # Git mode 160000 identifies a submodule; ordinary files and folders are not valid here.
    if len(fields) != 3 or fields[:2] != ["160000", "commit"]:
        raise Error(f"Required path is not a submodule in its parent: {name}")
    return fields[2]


def verify_sources(source, recipe):
    """Check all checked-out commits, parent Git links and tracked/untracked changes."""
    resolved = {}
    # Check each parent before deriving the expected commit for its children.
    names = sorted(recipe["submodules"], key=lambda item: (item.count("/"), item))
    for name in [".", *names]:
        directory = (
            source if name == "." else release.dependencies.local_path(source, name)
        )
        revision = (
            recipe["frida"]["revision"]
            if name == "."
            else submodule_revision(source, name, names)
        )
        if git(directory, "rev-parse", "HEAD") != revision:
            raise Error(f"Source commit differs from its expected commit: {name}")
        # Each required repository is checked separately; avoid walking its children twice.
        if git(
            directory,
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--ignore-submodules=all",
        ):
            raise Error(f"Source checkout contains changes: {name}")
        resolved[name] = revision
    return resolved


def checkout(source, recipe):
    """Clone the pinned release and only the submodules required by this build."""
    frida = recipe["frida"]
    git(
        source.parent,
        "clone",
        "--depth",
        "1",
        "--branch",
        frida["version"],
        frida["repository"],
        str(source),
    )
    if git(source, "rev-parse", "HEAD") != frida["revision"]:
        raise Error("The upstream release tag no longer matches its pinned commit")
    # Git checks out the commit recorded by each parent; initialise parents before children.
    for name in sorted(recipe["submodules"], key=lambda item: (item.count("/"), item)):
        owner = submodule_owner(name, recipe["submodules"])
        relative = (
            PurePosixPath(name).relative_to(owner).as_posix() if owner != "." else name
        )
        git(
            source / owner,
            "submodule",
            "update",
            "--init",
            "--checkout",
            "--depth",
            "1",
            "--",
            relative,
        )
    return verify_sources(source, recipe)


def pinned_archives(recipe, target):
    """Return exact filenames, download URLs and hashes needed for one build target."""
    return [
        {
            "name": name + ".tar.xz",
            "url": f"https://build.frida.re/deps/{recipe['frida']['deps_version']}/{name}.tar.xz",
            "sha256": recipe["archives"][name],
        }
        for name in recipe["targets"][target]
    ]


def stage_archives(cache, destination, records):
    """Check cached/downloaded archives and copy them into the fresh upstream cache."""
    destination.mkdir()
    for record in records:
        saved = release.dependencies.local_path(cache, record["name"])
        # Existing downloads are reused only if their pinned hash matches.
        release.download(record["url"], saved, record["sha256"])
        shutil.copyfile(saved, destination / saved.name)


def inspect_wheel(wheel, recipe, target):
    """Check wheel identity, platform, metadata, licence and required binding files."""
    name, version, _, tags = parse_wheel_filename(wheel.name)
    expected_platform = {
        "windows-x86_64": r"win_amd64",
        "linux-x86_64": r"linux_x86_64",
        "macos-x86_64": r"macosx_[0-9_]+_x86_64",
        "macos-arm64": r"macosx_[0-9_]+_arm64",
    }[target]
    if (
        name != "frida"
        or str(version) != recipe["frida"]["package_version"]
        or not tags
        or any(
            tag.interpreter != "cp37"
            or tag.abi != "abi3"
            or not re.fullmatch(expected_platform, tag.platform)
            for tag in tags
        )
    ):
        raise Error("Wheel version, stable ABI or platform differs from the recipe")
    with zipfile.ZipFile(wheel) as archive:
        members = release.archive_files(archive)
        metadata = [name for name in members if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise Error("Expected one Frida wheel metadata file")
        info = Parser().parsestr(archive.read(metadata[0]).decode("utf-8"))
        if (
            info["Name"] != "frida"
            or info["Version"] != recipe["frida"]["package_version"]
        ):
            raise Error("Wheel metadata differs from its filename")
        required = {
            "frida/__init__.py",
            "frida/aio.py",
            "frida/_frida.pyi",
            "frida/py.typed",
            metadata[0].replace("/METADATA", "/licenses/COPYING"),
        }
        binaries = [
            name
            for name in members
            if re.fullmatch(r"frida/_frida(?:\.abi3)?\.(?:pyd|so)", name)
        ]
        if not required.issubset(members) or len(binaries) != 1:
            raise Error("Wheel is missing its binding, generated files or licence")
        if archive.testzip() is not None:
            raise Error("Wheel contains damaged ZIP entries")
        extension = archive.read(binaries[0])
        return {
            "path": binaries[0],
            "size": len(extension),
            "sha256": hashlib.sha256(extension).hexdigest(),
        }


def file_record(path):
    """Return the filename, size and SHA-256 of a completed build file."""
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"name": path.name, "size": path.stat().st_size, "sha256": digest}


def verify_output(output, recipe, target):
    """Check recorded output against the current recipe; this does not verify a signature."""
    # Reject a redirected output folder before local_path resolves its root.
    if output.is_symlink() or output.is_junction():
        raise Error("Build output must be an ordinary directory")
    details = json.loads(
        release.dependencies.local_path(output, "build-details.json").read_text(
            encoding="utf-8"
        )
    )
    sources = details["sources"]
    if (
        details["recipe_sha256"] != recipe_digest(recipe)
        or details["target"] != target
        # Derived submodule commits remain build records, rather than another set of recipe pins.
        or not isinstance(sources, dict)
        or sources.get(".") != recipe["frida"]["revision"]
        or set(sources) != {".", *recipe["submodules"]}
        or any(
            not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{40}", revision)
            for revision in sources.values()
        )
        or details["inputs"] != pinned_archives(recipe, target)
    ):
        raise Error("Build details do not match the pinned recipe and target")
    wheel = release.dependencies.local_path(output, details["wheel"]["name"])
    if {path.name for path in output.iterdir()} != {wheel.name, "build-details.json"}:
        raise Error("Build output contains unexpected files")
    if file_record(wheel) != details["wheel"]:
        raise Error("Built wheel SHA-256 or size differs from build details")
    if inspect_wheel(wheel, recipe, target) != details["extension"]:
        raise Error("Built extension differs from build details")
    return wheel


def wheel_platform_args(extension, target):
    """Choose a macOS wheel tag from the native extension rather than universal Python."""
    if not target.startswith("macos-"):
        return []  # Keep the existing Windows/Linux packaging defaults.
    architecture = target.removeprefix("macos-")
    actual = subprocess.run(
        ["lipo", "-archs", str(extension)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    # Each build job packages one CPU, so lipo must report exactly that CPU.
    if actual != [architecture]:
        raise Error(f"Compiled extension architectures {actual} differ from {target}")

    from setuptools.command.bdist_wheel import get_platform

    # Setuptools reads the compiled Mach-O's minimum OS, keeping a higher Python floor too.
    platform_tag = get_platform(str(extension.parent))
    prefix = re.match(r"macosx_[0-9]+_[0-9]+_", platform_tag)
    if prefix is None:
        raise Error(f"Expected a macOS packaging platform, got {platform_tag}")
    return ["--plat-name", prefix[0] + architecture]


def build(recipe, target, work, output, cache):
    """Compile in a fresh short workspace and publish checked outputs without installing them."""
    if output.exists():
        wheel = verify_output(output, recipe, target)
        print("Reusing checked build output; compilation was not needed.")
        return wheel
    # Never overwrite or clean a caller's workspace. Short paths avoid Windows tool failures.
    if (
        work.is_symlink()
        or work.is_junction()
        or (work.exists() and any(work.iterdir()))
    ):
        raise Error("Build workspace must be an empty ordinary directory")
    work = work.resolve()
    if os.name == "nt" and len(str(work)) > 60:
        raise Error("Use a Windows workspace path of at most 60 characters")
    if (
        importlib.metadata.version("setuptools")
        != recipe["frida"]["setuptools_version"]
    ):
        raise Error("Install the pinned setuptools from requirements-dev.txt")
    node = subprocess.run(
        ["node", "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if int(node.lstrip("v").split(".")[0]) < 18:
        raise Error("Upstream Frida requires Node.js 18 or newer")
    work.mkdir(parents=True, exist_ok=True)
    source, products = work / "source", work / "build"
    sources = checkout(source, recipe)
    inputs = pinned_archives(recipe, target)
    stage_archives(cache, work / "deps", inputs)
    # Drop Frida/Python overrides so an unrelated developer environment cannot select another SDK.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("FRIDA_", "MESON_", "PYTHON", "SETUPTOOLS_"))
    }
    environment.update(
        FRIDA_DEPS=str(work / "deps"),
        FRIDA_RELENG=str(source / "releng"),
        npm_config_cache=str(work / "npm-cache"),
        PYTHON=sys.executable,
        MESON_BUILD_ROOT=str(products),
    )
    # Meson otherwise finds a global python3 shim, which may not have our pinned build tools.
    python_file = work / "build-python.ini"
    python_value = "'" + Path(sys.executable).as_posix().replace("'", "\\'") + "'"
    python_file.write_text(
        f"[binaries]\npython = {python_value}\npython3 = {python_value}\n",
        encoding="utf-8",
    )
    # Use upstream's command entry points in child interpreters, isolating their imports and environment.
    for module, arguments in (
        (
            "meson_configure",
            [
                "--prefix=" + str(work / "install"),
                "--",  # Pass the remaining options directly to Meson.
                *recipe["frida"]["options"],
                "--native-file",
                str(python_file),
            ],
        ),
        ("meson_make", [str(products), "all"]),
    ):
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, sys.argv[1]); from releng.{module} import main; main()",
                str(source),
                *arguments,
            ],
            cwd=source,
            env=environment,
            check=True,
        )
    extensions = [
        path
        for path in (products / "subprojects/frida-python/frida").glob("_frida.*")
        if path.suffix in (".pyd", ".so")
    ]
    if len(extensions) != 1:
        raise Error("Expected exactly one compiled Python extension")
    environment.update(
        FRIDA_EXTENSION=str(extensions[0]),
        FRIDA_VERSION=recipe["frida"]["package_version"],
    )
    # Use upstream's existing prebuilt-extension wheel packaging, not our own wheel writer.
    subprocess.run(
        [
            sys.executable,
            "-B",
            "setup.py",
            "build",
            "--build-base",
            str(work / "wheel-build"),
            "bdist_wheel",
            *wheel_platform_args(extensions[0], target),
            "--dist-dir",
            str(work / "wheels"),
        ],
        cwd=source / "subprojects/frida-python",
        env=environment,
        check=True,
    )
    wheels = list((work / "wheels").glob("*.whl"))
    if len(wheels) != 1:
        raise Error("Expected exactly one generated wheel")
    extension = inspect_wheel(wheels[0], recipe, target)
    if extension["sha256"] != file_record(extensions[0])["sha256"]:
        raise Error("Packaged extension differs from the compiled extension")
    # Retain compiler versions without publishing the developer's absolute filesystem paths.
    compilers = json.loads((products / "meson-info/intro-compilers.json").read_text())
    compiler_versions = {
        machine: {
            lang: {
                key: value
                for key, value in compiler.items()
                if key in ("id", "version", "linker_id", "linker_version")
            }
            for lang, compiler in languages.items()
        }
        for machine, languages in compilers.items()
    }
    details = {
        "recipe_sha256": recipe_digest(recipe),
        "target": target,
        "sources": sources,
        "inputs": inputs,
        "tools": {
            "python": platform.python_version(),
            "setuptools": importlib.metadata.version("setuptools"),
            "node": node,
            "git": git(source, "--version"),
            "compilers": compiler_versions,
        },
        "wheel": file_record(wheels[0]),
        "extension": extension,
    }
    # Publish through a new ordinary folder so pip/temp-folder permissions are not carried across.
    output.parent.mkdir(parents=True, exist_ok=True)
    with release.build_directory(output.parent, ".frida-output-") as stage:
        shutil.copyfile(wheels[0], stage / wheels[0].name)
        (stage / "build-details.json").write_text(
            json.dumps(details, indent=2) + "\n", encoding="utf-8"
        )
        verify_output(stage, recipe, target)
        stage.rename(output)
    return output / wheels[0].name


def main(argv=None):
    """Build or verify one native wheel; return a failed command status for any rejected input."""
    try:
        recipe = read_recipe()
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument(
            "--target",
            choices=recipe["targets"],
            help="native target; defaults to this host",
        )
        parser.add_argument(
            "--work-dir",
            type=Path,
            help="empty short build directory; retained for inspection",
        )
        parser.add_argument(
            "--output",
            type=Path,
            help="wheel/details directory; defaults to .cache/reduced-frida/<target>",
        )
        parser.add_argument(
            "--verify-only",
            action="store_true",
            help="check existing wheel/details without compiling or downloading",
        )
        args = parser.parse_args(argv)
        host = host_target(recipe) if not args.verify_only or not args.target else None
        target = args.target or host
        output = (args.output or ROOT / ".cache/reduced-frida" / target).absolute()
        if args.verify_only:
            wheel = verify_output(output, recipe, target)
        else:
            if target != host:
                raise Error(
                    "Run the recipe on its native OS/CPU; cross-compilation is not configured"
                )
            work = args.work_dir or Path(tempfile.gettempdir()) / (
                "mlf-" + uuid.uuid4().hex[:8]
            )
            if not output.exists():
                print(f"Build workspace: {work}", flush=True)
            cache = release.dependencies.local_path(
                ROOT, ".cache/reduced-frida/archives"
            )
            wheel = build(recipe, target, work, output, cache)
        print(f"Checked reduced Frida wheel: {wheel}")
        return 0
    except (
        Error,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
        importlib.metadata.PackageNotFoundError,
    ) as error:
        print(f"Reduced Frida build failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
