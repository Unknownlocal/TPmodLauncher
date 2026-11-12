# SPDX-License-Identifier: MPL-2.0
"""Reserved native Linux dialog adapter; console presentation is used instead."""


class LinuxDialog:
    """Keep the future dialog interface explicit without a desktop dependency."""

    def show(self, notice, notify):
        """Native Linux dialogs are deferred and must not be selected yet."""
        raise NotImplementedError("Native Linux error dialogs are not implemented")
