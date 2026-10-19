# SPDX-License-Identifier: MPL-2.0
"""Build admission for the private native project catalog, without game imports."""

import sys

import pytest

from hook_manager.manager import HookError
from hook_manager.regulations.projects import PROJECT_GAME_SHA256, project_targets


def test_project_targets_require_the_inspected_build_without_importing_game():
    before = set(sys.modules)
    with pytest.raises(HookError, match="do not support this game build"):
        project_targets("0" * 64)
    targets = project_targets(PROJECT_GAME_SHA256)
    assert not (set(sys.modules) - before) & {target.module for target in targets}
