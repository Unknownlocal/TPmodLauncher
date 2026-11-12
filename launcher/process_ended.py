# SPDX-License-Identifier: MPL-2.0
"""Separate retired-process control flow from errors attributed to mods."""


class ProcessEnded(BaseException):
    """Unwind abandoned work without treating normal game exit as a mod failure.

    This deliberately bypasses ordinary Exception handlers in mod preparation;
    the controller catches it and still consumes the process's final diagnostics.
    """
