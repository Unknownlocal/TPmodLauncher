# SPDX-License-Identifier: MPL-2.0
"""Trusted development-project mappings; importing this package installs no hooks."""

from .targets import PROJECT_GAME_SHA256, project_targets

__all__ = ["PROJECT_GAME_SHA256", "project_targets"]
