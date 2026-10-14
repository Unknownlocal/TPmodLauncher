# SPDX-License-Identifier: MPL-2.0
"""Export and restore pinned Frida/SDK sources for rebuilding and relinking."""

import argparse
import base64
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tomllib
from urllib.parse import urlsplit

if __package__:
    from . import build_frida as build
else:
    # Dynamic loading also works when the release tool imports this sibling by filename.
    spec = importlib.util.spec_from_file_location(
        "_source_builder", Path(__file__).with_name("build_frida.py")
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)

paths = build.release.dependencies


def library_name(argument):
    """Match linker arguments to SDK manifest filenames without their library suffix."""
    if argument.startswith("-l"):
        return argument[2:]
    name = Path(argument).name
    if not name.endswith((".a", ".lib", ".so", ".dylib")):
        return None
    return name.rsplit(".", 1)[0].removeprefix("lib")


def sdk_components(work):
    """Find SDK packages referenced by all native and compatibility build dependencies."""
    libraries = set()
    # Include compatibility helpers' own Meson records, not only the outer build.
    for record in (work / "build").rglob("intro-dependencies.json"):
        for dependency in json.loads(record.read_text()):
            libraries.update(
                filter(None, map(library_name, dependency.get("link_args", [])))
            )
    if not libraries:
        raise paths.DependencyError(
            "No native dependency records found in the build workspace"
        )
    components = set()
    for manifest in (work / "deps").glob("sdk-*/manifest/*.pkg"):
        if any(
            library_name(line) in libraries
            for line in manifest.read_text().splitlines()
        ):
            components.add(manifest.stem)
    if not components:
        raise paths.DependencyError("Native libraries did not match any SDK manifests")
    return sorted(components)


def repositories(root):
    """List a repository and its checked-out submodules with commits derived from parents."""
    found = {".": build.git(root, "rev-parse", "HEAD")}

    def visit(directory, prefix):
        for line in build.git(directory, "ls-tree", "-r", "HEAD").splitlines():
            fields, _, name = line.partition("\t")
            mode, kind, commit = fields.split()
            if mode != "160000":
                continue
            relative = name if prefix == "." else prefix + "/" + name
            child = paths.local_path(root, relative)
            if build.git(child, "rev-parse", "HEAD") != commit:
                raise paths.DependencyError(
                    f"Source submodule differs from its parent: {relative}"
                )
            found[relative] = commit
            visit(child, relative)

    visit(root, ".")
    for name in found:
        if build.git(
            root / name,
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--ignore-submodules=all",
        ):
            raise paths.DependencyError(f"Source repository contains changes: {name}")
    return found


def export_repository(repository, destination):
    """Save Git objects and shallow boundaries, excluding local config, hooks and paths."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    tags = build.git(
        repository,
        "for-each-ref",
        "--points-at",
        "HEAD",
        "--format=%(refname)",
        "refs/tags",
    ).splitlines()
    build.git(repository, "bundle", "create", str(destination), "HEAD", *tags)
    # Bundles do not preserve shallow boundaries; retain them so restored Git history is valid.
    shallow = Path(build.git(repository, "rev-parse", "--git-path", "shallow"))
    if not shallow.is_absolute():
        shallow = repository / shallow
    if shallow.exists():
        destination.with_suffix(".shallow").write_bytes(shallow.read_bytes())


def verify_npm_archive(saved, installed):
    """Check the saved package's files against its installed build inputs."""
    # saved is the registry archive; installed is the package folder in node_modules.
    seen, total = set(), 0
    with tarfile.open(saved) as archive:
        for member in archive:
            relative = paths.safe_name(member.name)
            if relative.parts[0] != "package":
                raise paths.DependencyError("Expected npm package/ archive layout")
            if member.isdir():
                continue
            relative = relative.relative_to("package").as_posix()
            total += member.size
            # Reject links, repeated filenames and oversized contents before reading them.
            if (
                not member.isfile()
                or relative.casefold() in seen
                or total > 32 * 1024 * 1024
            ):
                raise paths.DependencyError("Unsupported npm source archive entry")
            seen.add(relative.casefold())
            actual = paths.local_path(installed, relative)
            # Compare every published file, including metadata, with the actual build input.
            with archive.extractfile(member) as source:
                if source.read() != actual.read_bytes():
                    raise paths.DependencyError(
                        f"Built npm source differs from its published archive: {relative}"
                    )
    if "package.json" not in seen:
        raise paths.DependencyError(
            "npm source archive is missing its package metadata"
        )


def export_npm(work, target, content):
    """Preserve incorporated Darwin npm packages, matching their locks and installed source files."""
    if not target.startswith("macos-"):
        return  # Windows/Linux do not compile these Darwin agents.
    expected = {"reportcrash.js", "osanalytics.js"}
    found = set()
    archives = {}
    for record in (work / "build").rglob("intro-targets.json"):
        for entry in json.loads(record.read_text()):
            agent = entry["name"]
            if agent not in expected:
                continue
            (filename,) = entry["filename"]
            output = paths.local_path(work, Path(filename).relative_to(work).as_posix())
            if not output.is_file():
                raise paths.DependencyError(f"Built Darwin agent is missing: {agent}")
            private = output.parent / (agent + ".p")
            original = (
                work / "source/subprojects/frida-core/src/darwin/agent" / agent[:-3]
            )
            locked = json.loads((original / "package-lock.json").read_text())[
                "packages"
            ]
            installed = json.loads((private / "package-lock.json").read_text())[
                "packages"
            ]
            for name, package in locked.items():
                if not name or package.get("dev", False):
                    continue  # Build tools such as esbuild are not incorporated into the agent.
                if not name.startswith("node_modules/"):
                    raise paths.DependencyError("Unsupported npm package location")
                current = installed.get(name, {})
                if any(
                    current.get(key) != package[key]
                    for key in ("version", "resolved", "integrity")
                ):
                    raise paths.DependencyError(
                        f"Built npm lock differs from the upstream pin: {name}"
                    )
                integrity = package["integrity"]
                algorithm, _, encoded = integrity.partition("-")
                digest = base64.b64decode(encoded, validate=True)
                if (
                    algorithm != "sha512"
                    or len(digest) != 64
                    or not package["resolved"].startswith("https://registry.npmjs.org/")
                ):
                    raise paths.DependencyError(
                        "Expected npm registry source with a SHA-512 integrity pin"
                    )
                # Keep the registry's filename rather than inventing a package-name encoding.
                filename = Path(urlsplit(package["resolved"]).path).name
                saved = paths.local_path(
                    build.ROOT, ".cache/reduced-frida/npm/" + digest.hex()[:32] + ".tgz"
                )
                build.release.download(
                    package["resolved"], saved, digest.hex(), hash_name="sha512"
                )
                verify_npm_archive(saved, paths.local_path(private, name))
                destination = paths.local_path(content, "deps/npm/" + filename)
                if filename in archives and archives[filename] != integrity:
                    raise paths.DependencyError(
                        "Darwin agents used different npm archives for the same package"
                    )
                if filename not in archives:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(saved, destination)
                    archives[filename] = integrity
            found.add(agent)
    if found != expected or not archives:
        raise paths.DependencyError(
            "Missing Darwin agent build records or incorporated npm sources"
        )


def create(work, build_output, output):
    """Export sources matching one checked build into a new archive/details directory."""
    recipe = build.read_recipe()
    details_path = build_output / "build-details.json"
    details = json.loads(details_path.read_text())
    build.verify_output(build_output, recipe, details["target"])
    if build.verify_sources(work / "source", recipe) != details["sources"]:
        raise paths.DependencyError("Source workspace does not match the built wheel")
    if output.exists():
        raise paths.DependencyError(
            "Use a fresh source output directory; existing evidence is preserved"
        )
    dependencies = tomllib.loads((work / "source/releng/deps.toml").read_text())
    if dependencies["dependencies"]["version"] != recipe["frida"]["deps_version"]:
        raise paths.DependencyError(
            "Source dependency version differs from the checked SDKs"
        )
    components = sdk_components(work)
    cache = paths.local_path(build.ROOT, ".cache/reduced-frida/source-checkouts")
    cache.mkdir(parents=True, exist_ok=True)
    source_records = {
        "frida" if name == "." else "frida/" + name: commit
        for name, commit in details["sources"].items()
    }
    for name in components:
        package = dependencies[name]
        commit, url = package["version"], package["url"]
        # Only pinned Git commits in the reviewed upstream dependency definitions are accepted.
        if not re.fullmatch("[0-9a-f]{40}", commit) or not url.startswith(
            "https://github.com/"
        ):
            raise paths.DependencyError(f"Unsupported SDK source: {name}")
        repository = paths.local_path(cache, name + "-" + commit)
        if not repository.exists():
            repository.mkdir()
            build.git(repository, "init")
            build.git(repository, "remote", "add", "origin", url)
            build.git(repository, "fetch", "--depth", "1", "origin", commit)
            build.git(repository, "checkout", "FETCH_HEAD")
            build.git(
                repository,
                "submodule",
                "update",
                "--init",
                "--recursive",
                "--depth",
                "1",
            )
        if build.git(repository, "rev-parse", "HEAD") != commit:
            raise paths.DependencyError(
                f"SDK source commit differs from its pin: {name}"
            )
        for child, revision in repositories(repository).items():
            destination = "deps/src/" + name + ("/" + child if child != "." else "")
            source_records[destination] = revision
    output.parent.mkdir(parents=True, exist_ok=True)
    with build.release.build_directory(output.parent, ".sources-stage-") as stage:
        content = stage / "content"
        export_npm(work, details["target"], content)
        for name in source_records:
            if name.startswith("frida"):
                repository = work / "source" / (name[6:] if name != "frida" else ".")
            else:
                package_name, _, child = name[9:].partition("/")
                repository = (
                    cache
                    / (package_name + "-" + dependencies[package_name]["version"])
                    / child
                )
            if build.git(repository, "rev-parse", "HEAD") != source_records[name]:
                raise paths.DependencyError("Source commit changed during export")
            export_repository(repository, content / "repos" / (name + ".bundle"))
        # Ship the exact recipe, build scripts and original notices needed to inspect and rebuild.
        for name in (
            "build_frida.py",
            "frida_sources.py",
            "prepare_release.py",
            "bundled_dependencies.py",
            "frida_build.toml",
            "FRIDA_BUILD.md",
            "FRIDA_RELINK.md",
        ):
            target = content / "tools/launcher" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(build.ROOT / "launcher" / name, target)
        for name in ("requirements-dev.txt", "pyproject.toml", "LICENCE"):
            shutil.copyfile(build.ROOT / name, content / "tools" / name)
        shutil.copytree(
            build.ROOT / "notices",
            content / "tools/notices",
            copy_function=shutil.copyfile,
        )
        (content / "source-index.json").write_text(
            json.dumps(source_records, indent=2) + "\n"
        )
        shutil.copyfile(details_path, content / "build-details.json")
        archive = stage / "frida-sources.tar.xz"
        with tarfile.open(archive, "w:xz") as package:
            for item in sorted(content.rglob("*")):
                if item.is_file():
                    info = package.gettarinfo(
                        str(item), arcname=item.relative_to(content).as_posix()
                    )
                    # Source artefacts must not publish the local user's identity or timestamps.
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    info.mode = 0o644
                    with item.open("rb") as source:
                        package.addfile(info, source)
        # Remove only the ordinary staging content created above, inside this staging folder.
        paths.local_path(stage, "content")
        if not content.resolve().is_relative_to(stage.resolve()):
            raise paths.DependencyError("Source staging escaped its parent")
        for _ in paths.tree_paths(content):
            pass
        shutil.rmtree(content)
        receipt = {
            "build_details_sha256": build.file_record(details_path)["sha256"],
            "archive": build.file_record(archive),
        }
        (stage / "source-details.json").write_text(json.dumps(receipt, indent=2) + "\n")
        stage.rename(output)
    return output / "frida-sources.tar.xz"


def verify(output, build_output):
    """Check that the source archive receipt matches the exact binary build record."""
    if output.is_symlink() or output.is_junction():
        raise paths.DependencyError("Source output must be an ordinary directory")
    receipt = json.loads(paths.local_path(output, "source-details.json").read_text())
    archive = paths.local_path(output, receipt["archive"]["name"])
    if (
        receipt["build_details_sha256"]
        != build.file_record(build_output / "build-details.json")["sha256"]
        or build.file_record(archive) != receipt["archive"]
    ):
        raise paths.DependencyError(
            "Source package differs from its receipt or binary build"
        )
    return archive


def restore(archive, destination):
    """Restore source repositories locally, without fetching any code from the network."""
    if destination.exists():
        raise paths.DependencyError("Source restoration needs a fresh destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with build.release.build_directory(destination.parent, ".restore-stage-") as stage:
        with tarfile.open(archive) as package:
            seen, total = set(), 0
            for member in package:
                name = paths.safe_name(member.name).as_posix()
                total += member.size
                if (
                    not member.isfile()
                    or name.casefold() in seen
                    or total > 512 * 1024 * 1024
                ):
                    raise paths.DependencyError(
                        "Unsupported or oversized source archive entry"
                    )
                seen.add(name.casefold())
                target = paths.local_path(stage, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        records = json.loads((stage / "source-index.json").read_text())
        if not isinstance(records, dict) or not records:
            raise paths.DependencyError(
                "Source archive needs a nonempty repository index"
            )
        for name, commit in sorted(
            records.items(), key=lambda row: (row[0].count("/"), row[0])
        ):
            if (
                not isinstance(commit, str)
                or not re.fullmatch("[0-9a-f]{40}", commit)
                or not (name == "frida" or name.startswith(("frida/", "deps/src/")))
            ):
                raise paths.DependencyError("Invalid source repository record")
            repository = paths.local_path(stage, name)
            bundle = paths.local_path(stage, "repos/" + name + ".bundle")
            if repository.exists() and any(repository.iterdir()):
                raise paths.DependencyError(
                    "Unexpected files in a source checkout destination"
                )
            repository.mkdir(parents=True, exist_ok=True)
            # Init/fetch avoids Git clone's fixed GIT_DIR path limit on Windows.
            build.git(repository, "init")
            shallow = bundle.with_suffix(".shallow")
            if shallow.exists():
                (repository / ".git/shallow").write_bytes(shallow.read_bytes())
            tags = [
                line.split()[1]
                for line in build.git(
                    stage, "bundle", "list-heads", str(bundle)
                ).splitlines()
                if line.split()[1].startswith("refs/tags/")
            ]
            build.git(
                repository,
                "fetch",
                str(bundle),
                "HEAD",
                *(tag + ":" + tag for tag in tags),
            )
            build.git(repository, "checkout", "--detach", commit)
            build.git(repository, "fsck", "--no-reflogs")
        # Point remotes at the final bundled-object location before moving the staged tree.
        for name in records:
            build.git(
                stage / name,
                "remote",
                "add",
                "origin",
                str(destination / "repos" / (name + ".bundle")),
            )
        stage.rename(destination)
    return destination


def main():
    """Create a source package or restore it for inspection and local library changes."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create_command = commands.add_parser("create")
    create_command.add_argument("--work-dir", required=True, type=Path)
    create_command.add_argument("--build-output", required=True, type=Path)
    create_command.add_argument("--output", required=True, type=Path)
    restore_command = commands.add_parser("restore")
    restore_command.add_argument("archive", type=Path)
    restore_command.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create(
                args.work_dir.resolve(),
                args.build_output.absolute(),
                args.output.absolute(),
            )
        else:
            result = restore(args.archive.resolve(), args.output.absolute())
        print(result)
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        paths.DependencyError,
        subprocess.CalledProcessError,
        tarfile.TarError,
    ) as error:
        print(f"Source packaging failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
