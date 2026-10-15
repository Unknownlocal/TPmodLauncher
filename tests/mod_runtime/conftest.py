# SPDX-License-Identifier: MPL-2.0
"""Build the fixed trusted runner once for real boundary tests."""

import pytest

from mod_runtime.build import build


@pytest.fixture(scope="session")
def runner_bundle(tmp_path_factory):
    return build(tmp_path_factory.mktemp("runner-build") / "bundle")
