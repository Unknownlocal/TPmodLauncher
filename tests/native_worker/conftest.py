# SPDX-License-Identifier: MPL-2.0
"""Require a prepared native worker for owned-process transport tests."""

import os
from pathlib import Path
import pytest
from launcher.native_worker import verify_worker, WorkerError


@pytest.fixture
def worker_executable():
    try:
        return verify_worker(Path(__file__).resolve().parents[2])
    except WorkerError as error:
        if os.environ.get("TPM_REQUIRE_NATIVE_TESTS") == "1":
            pytest.fail(str(error))
        pytest.skip("Build the native Core worker to run its integration tests")
