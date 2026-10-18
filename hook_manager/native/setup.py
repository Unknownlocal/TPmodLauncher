# SPDX-License-Identifier: MPL-2.0
"""Build the optional internal collector with CPython's 3.13 stable ABI."""

from pathlib import Path
import os

from setuptools import Extension, setup


setup(
    name="modlauncher-race-collector",
    version="0.1.0",
    ext_modules=[Extension(
        "_race_profile",
        [os.path.relpath(Path(__file__).with_name("profiling.c"))],
        define_macros=[("Py_LIMITED_API", "0x030D0000")],
        py_limited_api=True,
    )],
)
