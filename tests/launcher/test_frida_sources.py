# SPDX-License-Identifier: MPL-2.0
"""Exercise source export/restoration with real Git objects and package receipts."""

import io
import base64
import hashlib
import json
import tarfile

import pytest

from launcher import frida_sources as sources

pytestmark = pytest.mark.release


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


def test_source_receipt_is_bound_to_archive_and_exact_binary_build(tmp_path):
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
    archive.write_bytes(b"changed")
    with pytest.raises(sources.paths.DependencyError, match="differs"):
        sources.verify(output, binary)


def test_unsafe_source_archive_cannot_publish_or_escape(tmp_path):
    archive = tmp_path / "unsafe.tar.xz"
    with tarfile.open(archive, "w:xz") as package:
        member = tarfile.TarInfo("../outside")
        member.size = 3
        package.addfile(member, io.BytesIO(b"one"))
    destination = tmp_path / "restored"
    with pytest.raises(sources.paths.DependencyError):
        sources.restore(archive, destination)
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
