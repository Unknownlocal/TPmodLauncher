# SPDX-License-Identifier: MPL-2.0
"""Check the native drawing helpers after a negotiation screen imports normally."""

from ....callables import method_group
from ...ui import ui_bindings


def feedback_bindings(screen, kind):
    ui = ui_bindings(screen)
    # Common drawing checks live with the reusable widgets. Negotiation hooks
    # and the layout anchors used by PersonnelNotice stay specific to personnel.
    signatures = {
        "handle_event": "(self, event)", "draw": "(self, surface)",
        "_pipeline_record": "(self)",
    }
    if kind == "driver":
        signatures.update({
            "_section_header": "(self, surface, text, x, y, width, *, color=(240, 240, 240))",
            "_interest_threshold": "(self) -> float",
            "_button": "(self, surface, rect, label, *, primary=False, enabled=True, danger=False)",
        })
    else:
        signatures.update({
            "action_feedback": "(self, successful)",
            "_draw_clipped": "(self, surface, font, text, color, x, y, max_width)",
            "_draw_action_buttons": "(self, surface, submit_label, can_submit)",
        })
    method_group(screen, signatures)
    return ui
