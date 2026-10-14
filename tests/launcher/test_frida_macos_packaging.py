# SPDX-License-Identifier: MPL-2.0
"""Check native macOS wheel tags using real Mach-O headers and setuptools."""

import struct
import subprocess
import sysconfig

import pytest
from setuptools import Distribution, Extension
from setuptools.command.bdist_wheel import bdist_wheel, get_platform

from launcher import build_frida as build


@pytest.fixture
def macos_extension(tmp_path, monkeypatch):
    """Write a small dylib header and emulate the Mac-only lipo command."""
    monkeypatch.delenv("MACOSX_DEPLOYMENT_TARGET", raising=False)
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "macosx-10.13-universal2")
    original_run = subprocess.run

    def create(architecture, minimum):
        extension = tmp_path / "_frida.abi3.so"
        cpu = {"x86_64": 0x01000007, "arm64": 0x0100000C}[architecture]
        version = (minimum[0] << 16) | (minimum[1] << 8)
        # A 64-bit dylib with one LC_BUILD_VERSION command; no native code is executed.
        extension.write_bytes(
            struct.pack("<8I", 0xFEEDFACF, cpu, 0, 6, 1, 24, 0, 0)
            + struct.pack("<6I", 0x32, 24, 1, version, version, 0)
        )

        def run(command, **kwargs):
            if command == ["lipo", "-archs", str(extension)]:
                return subprocess.CompletedProcess(command, 0, architecture + "\n")
            return original_run(command, **kwargs)

        monkeypatch.setattr(build.subprocess, "run", run)
        return extension

    return create


@pytest.mark.parametrize(
    "architecture,minimum,expected",
    [
        ("x86_64", (10, 9), "macosx_10_13_x86_64"),
        ("x86_64", (10, 15), "macosx_10_15_x86_64"),
        ("arm64", (11, 0), "macosx_11_0_arm64"),
    ],
)
def test_native_wheel_uses_binary_minimum_without_universal_python_architecture(
    macos_extension, architecture, minimum, expected
):
    extension = macos_extension(architecture, minimum)
    # The original setuptools default reproduces the CI failure's universal2 suffix.
    assert get_platform(str(extension.parent)).endswith("_universal2")
    arguments = build.wheel_platform_args(extension, "macos-" + architecture)
    assert arguments == ["--plat-name", expected]

    # Exercise the actual wheel command's option handling, not a fake packaging result.
    distribution = Distribution(
        {"ext_modules": [Extension("frida._frida", sources=[], py_limited_api=True)]}
    )
    command = bdist_wheel(distribution)
    command.py_limited_api = "cp37"
    command.plat_name = arguments[1]
    command.ensure_finalized()
    assert command.get_tag() == ("cp37", "abi3", expected)


def test_wrong_binary_architecture_is_rejected_before_relabelling(macos_extension):
    extension = macos_extension("x86_64", (10, 13))
    with pytest.raises(build.Error, match="architectures.*differ"):
        build.wheel_platform_args(extension, "macos-arm64")


@pytest.mark.parametrize("target", ["windows-x86_64", "linux-x86_64"])
def test_other_platforms_keep_default_packaging_without_macos_tools(tmp_path, target):
    assert build.wheel_platform_args(tmp_path / "absent-extension", target) == []
