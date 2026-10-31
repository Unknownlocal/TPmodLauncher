# SPDX-License-Identifier: MPL-2.0
"""Native refusal formats chosen by trusted game adapters, never mod data."""


def refuse_boolean(message):
    """Return the game's boolean failure value, regardless of the display text."""
    return False


def refuse_with_reason(message):
    """Return a fresh failure/reason pair for native contract decision methods."""
    return False, message
