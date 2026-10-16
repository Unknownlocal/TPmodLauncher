# SPDX-License-Identifier: MPL-2.0
"""Define the supported mod API versions, permissions and lifecycle signatures."""

# This names the newest supported contract; older versions stay listed below.
API_VERSION = "0.3.0"

# Each version lists the permissions a mod may request in its manifest.
CAPABILITIES = {
    "0.1.0": {"diagnostics"},
    "0.2.0": {"diagnostics", "main-menu"},
    API_VERSION: {"diagnostics", "main-menu", "ui-settings"},
}

# Each export maps to (parameter types, return type); None means no return value.
# Both supported versions currently use these same lifecycle signatures.
EXPORTS = {"activate": ((), None), "on-event": (("string", "string"), "string")}
