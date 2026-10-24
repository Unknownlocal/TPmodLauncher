# SPDX-License-Identifier: MPL-2.0
"""Private project catalog construction never imports or gates the game image."""

import sys

from hook_manager.game_adapters.projects import project_targets


def test_project_targets_resolve_individual_methods_without_importing_game():
    before = set(sys.modules)
    targets = project_targets()
    assert len(targets) == 3
    assert not (set(sys.modules) - before) & {target.module for target in targets}
