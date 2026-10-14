# SPDX-License-Identifier: MPL-2.0
"""Exercise source export/restoration with real Git objects and package receipts."""

import io
import json
from pathlib import Path
import tarfile

import pytest

from launcher import frida_sources as sources


def source_archive(content, output):
    """Pack ordinary fixture files just as the source exporter does."""
    with tarfile.open(output, "w:xz") as archive:
        for file in content.rglob("*"):
            if file.is_file():
                archive.add(file, arcname=file.relative_to(content).as_posix())
    return output


def test_shallow_tag_and_nested_sources_restore_without_network(
    tmp_path, source_repository
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
