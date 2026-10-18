# SPDX-License-Identifier: MPL-2.0
"""Load the optional production collector for native integration tests."""

import importlib
import os
from pathlib import Path
import sys

import pytest


@pytest.fixture(scope="module")
def native():
    directory = os.environ.get("MODLAUNCHER_PROFILE_NATIVE")
    if not directory:
        if os.environ.get("TPM_REQUIRE_PROFILE_NATIVE_TESTS") == "1":
            pytest.fail("Required native collector build was not configured")
        pytest.skip("Build optional collector and set MODLAUNCHER_PROFILE_NATIVE")
    directory = str(Path(directory).resolve(strict=True))
    sys.path.insert(0, directory)
    try:
        backend = importlib.import_module("_race_profile")
    finally:
        sys.path.remove(directory)
    assert Path(backend.__file__).parent == Path(directory)
    return backend
