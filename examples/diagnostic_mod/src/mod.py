# SPDX-License-Identifier: MPL-2.0
"""An author example: sandboxed Python using generated WIT bindings and the standard library."""

import json
import wit_world


class WitWorld(wit_world.WitWorld):
    def activate(self):
        # The host labels this message with the manifest's identity.
        wit_world.report("Diagnostic example activated")

    def on_event(self, name, payload):
        # Real event adapters define their accepted payloads and responses.
        return json.dumps({"event": name, "received": json.loads(payload)})
