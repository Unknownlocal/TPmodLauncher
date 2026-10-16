# SPDX-License-Identifier: MPL-2.0
"""Exercise source export/restoration with real Git objects and package receipts."""

import io
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from launcher import frida_sources as sources


def test_exported_build_tools_import_without_launcher_runtime(tmp_path):
    source = Path(__file__).resolve().parents[2] / "launcher"
    tools = tmp_path / "tools/launcher"
    tools.mkdir(parents=True)
    for name in ("build_frida.py", "prepare_release.py", "bundled_dependencies.py", "frida_build.toml"):
        shutil.copyfile(source / name, tools / name)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(tools / "build_frida.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and "--target" in result.stdout, result.stderr


def source_archive(content, output):
    """Pack ordinary fixture files just as the source exporter does."""
    with tarfile.open(output, "w:xz") as archive:
        for file in content.rglob("*"):
            if file.is_file():
                archive.add(file, arcname=file.relative_to(content).as_posix())
    return output


def test_shallow_tag_nested_and_npm_sources_restore_without_network(
    tmp_path, source_repository, darwin_npm_build
):
    child = tmp_path / "child"
    child_commit = source_repository(child)
    original = tmp_path / "original"
    source_repository(original)
    sources.build.git(
        original, "update-index", "--add", "--cacheinfo", f"160000,{child_commit},child"
    )
    sources.build.git(original, "commit", "-m", "Child source")
    commit = sources.build.git(original, "rev-parse", "HEAD")
    sources.build.git(original, "tag", "17.23.3")
    shallow = tmp_path / "shallow"
    sources.build.git(
        tmp_path,
        "clone",
        "--depth",
        "1",
        "--branch",
        "17.23.3",
        original.as_uri(),
        str(shallow),
    )
    content = tmp_path / "content"
    work, npm_archive = darwin_npm_build
    sources.export_npm(work, "macos-arm64", content)
    assert len(list((content / "deps/npm").iterdir())) == 1
    sources.export_repository(shallow, content / "repos/frida.bundle")
    sources.export_repository(child, content / "repos/frida/child.bundle")
    (content / "source-index.json").write_text(
        json.dumps({"frida": commit, "frida/child": child_commit})
    )
    restored = sources.restore(
        source_archive(content, tmp_path / "sources.tar.xz"), tmp_path / "restored"
    )
    assert sources.build.git(
        restored / "frida", "describe", "--tags", "--always", "--long"
    ).startswith("17.23.3-0-g")
    assert (
        sources.build.git(restored / "frida/child", "rev-parse", "FETCH_HEAD")
        == child_commit
    )
    assert (restored / "frida/child/code.py").read_text() == "VALUE = 1\n"
    assert (restored / "deps/npm/demo-1.0.0.tgz").read_bytes() == npm_archive
    assert sources.repositories(restored / "frida") == {
        ".": commit,
        "child": child_commit,
    }
    # Recipients receive editable source, not only a pointer to a remote repository.
    (restored / "frida/child/code.py").write_text("VALUE = 42\n")
    with pytest.raises(sources.paths.DependencyError, match="contains changes"):
        sources.repositories(restored / "frida")


def test_sdk_selection_includes_compatibility_libraries_and_excludes_unused_packages(
    tmp_path,
):
    for directory, arguments in (
        ("build/meson-info", ["-lglib-2.0", "C:/sdk/lib/libgioopenssl.a"]),
        ("build/compat/x86/meson-info", ["C:/sdk/lib/libtcc.a"]),
    ):
        info = tmp_path / directory
        info.mkdir(parents=True)
        (info / "intro-dependencies.json").write_text(
            json.dumps([{"link_args": arguments}])
        )
    manifests = tmp_path / "deps/sdk-windows-x86-mt/manifest"
    manifests.mkdir(parents=True)
    for name, library in (
        ("glib", "libglib-2.0.a"),
        ("glib-networking", "libgioopenssl.a"),
        ("tinycc", "libtcc.a"),
        ("v8", "libv8.a"),
    ):
        (manifests / (name + ".pkg")).write_text("lib/" + library + "\n")
    assert sources.sdk_components(tmp_path) == ["glib", "glib-networking", "tinycc"]


@pytest.mark.parametrize("damage", ["archive", "build"])
def test_source_receipt_is_bound_to_archive_and_exact_binary_build(tmp_path, damage):
    output, binary = tmp_path / "source", tmp_path / "binary"
    output.mkdir()
    binary.mkdir()
    archive = output / "frida-sources.tar.xz"
    archive.write_bytes(b"source fixture")
    details = binary / "build-details.json"
    details.write_bytes(b"binary fixture")
    (output / "source-details.json").write_text(
        json.dumps(
            {
                "archive": sources.build.file_record(archive),
                "build_details_sha256": sources.build.file_record(details)["sha256"],
            }
        )
    )
    assert sources.verify(output, binary) == archive
    (archive if damage == "archive" else details).write_bytes(b"changed")
    with pytest.raises(sources.paths.DependencyError, match="differs"):
        sources.verify(output, binary)


@pytest.mark.parametrize("damage", ["outside", "link", "duplicate"])
def test_unsafe_source_archive_cannot_publish_or_escape(tmp_path, damage):
    archive = tmp_path / "unsafe.tar.xz"
    with tarfile.open(archive, "w:xz") as package:
        member = tarfile.TarInfo("../outside" if damage == "outside" else "entry")
        if damage == "link":
            member.type, member.linkname = tarfile.SYMTYPE, "../outside"
            package.addfile(member)
        else:
            member.size = 3
            package.addfile(member, io.BytesIO(b"one"))
            if damage == "duplicate":
                package.addfile(member, io.BytesIO(b"two"))
    destination = tmp_path / "restored"
    with pytest.raises(sources.paths.DependencyError):
        sources.restore(archive, destination)
    assert not destination.exists() and not (tmp_path / "outside").exists()


def test_existing_restored_work_is_preserved(tmp_path):
    destination = tmp_path / "restored"
    destination.mkdir()
    marker = destination / "modified-library.c"
    marker.write_text("user modifications")
    with pytest.raises(sources.paths.DependencyError, match="fresh destination"):
        sources.restore(tmp_path / "absent-source-package", destination)
    assert marker.read_text() == "user modifications"


@pytest.mark.parametrize("index", [{}, {"frida": False}, {"../outside": "1" * 40}])
def test_invalid_repository_index_cannot_publish_checkouts(tmp_path, index):
    content = tmp_path / "content"
    content.mkdir()
    (content / "source-index.json").write_text(json.dumps(index))
    destination = tmp_path / "restored"
    with pytest.raises(sources.paths.DependencyError):
        sources.restore(
            source_archive(content, tmp_path / "sources.tar.xz"), destination
        )
    assert not destination.exists() and not (tmp_path / "outside").exists()


@pytest.fixture
def darwin_npm_build(tmp_path, monkeypatch):
    """Create real target records, locks, installed files and one integrity-pinned cache archive."""
    monkeypatch.setattr(sources.build, "ROOT", tmp_path)
    files = {
        "package.json": b'{"name":"demo","version":"1.0.0"}',
        "index.js": b"export const value = 42;\n",
    }
    saved = tmp_path / "package.tgz"
    with tarfile.open(saved, "w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo("package/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    data = saved.read_bytes()
    digest = hashlib.sha512(data).digest()
    cache = tmp_path / ".cache/reduced-frida/npm" / (digest.hex()[:32] + ".tgz")
    cache.parent.mkdir(parents=True)
    saved.rename(cache)
    locked = {
        "version": "1.0.0",
        "resolved": "https://registry.npmjs.org/nonexistent-fixture/-/demo-1.0.0.tgz",
        "integrity": "sha512-" + base64.b64encode(digest).decode(),
    }
    records = []
    work = tmp_path / "work"
    for name in ("reportcrash", "osanalytics"):
        output = work / "build" / name / (name + ".js")
        output.parent.mkdir(parents=True)
        output.write_text("synthetic bundled agent")
        records.append({"name": name + ".js", "filename": [str(output)]})
        original = work / "source/subprojects/frida-core/src/darwin/agent" / name
        private = output.parent / (name + ".js.p")
        original.mkdir(parents=True)
        package = private / "node_modules/demo"
        package.mkdir(parents=True)
        # A dev-only compiler must neither be downloaded nor included as runtime source.
        lock = {
            "packages": {
                "": {},
                "node_modules/demo": locked,
                "node_modules/esbuild": {"dev": True},
            }
        }
        for path in (original, private):
            (path / "package-lock.json").write_text(json.dumps(lock))
        for filename, content in files.items():
            (package / filename).write_bytes(content)
    info = work / "build/meson-info"
    info.mkdir()
    (info / "intro-targets.json").write_text(json.dumps(records))
    return work, data


@pytest.mark.parametrize(
    "damage", ["installed-source", "installed-lock", "missing-agent"]
)
def test_changed_or_incomplete_npm_build_cannot_export(
    darwin_npm_build, tmp_path, damage
):
    work, _ = darwin_npm_build
    private = work / "build/reportcrash/reportcrash.js.p"
    if damage == "installed-source":
        (private / "node_modules/demo/index.js").write_text("changed compiled input")
    elif damage == "installed-lock":
        path = private / "package-lock.json"
        lock = json.loads(path.read_text())
        lock["packages"]["node_modules/demo"]["version"] = "2.0.0"
        path.write_text(json.dumps(lock))
    else:
        (work / "build/reportcrash/reportcrash.js").unlink()
    with pytest.raises(sources.paths.DependencyError):
        sources.export_npm(work, "macos-x86_64", tmp_path / "content")


def test_non_darwin_export_does_not_request_npm_material(tmp_path):
    sources.export_npm(tmp_path / "absent-work", "linux-x86_64", tmp_path / "content")
    assert not (tmp_path / "content").exists()
