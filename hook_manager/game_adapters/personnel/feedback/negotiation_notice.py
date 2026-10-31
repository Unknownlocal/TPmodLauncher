# SPDX-License-Identifier: MPL-2.0
"""Retain a full refusal after closing its dialog, without moving native controls."""

from ...ui import ScrollableDialog, ScrollText


class NegotiationNotice:
    def __init__(self, ui, kind):
        self.ui, self.kind, self.reason = ui, kind, None
        self.approach_refusal = False
        self.inline, self.dialog = ScrollText(ui), ScrollableDialog(ui)

    def refuse(self, reason, *, approach=False):
        self.reason = reason
        self.approach_refusal = approach
        self.inline.reset()
        self.dialog.body.reset()
        self.dialog.open = True

    def confirm_approach(self, screen, original):
        """Show staff approach failures while keeping native text and sound.

        The native method returns None for success and failure. Observe its
        existing action feedback only during this call, then restore the screen's
        inherited or instance helper even if native code raises.
        """
        if getattr(screen, "view_only", False):
            return original()
        outcome = None
        feedback = screen.action_feedback
        missing = object()
        previous = vars(screen).get("action_feedback", missing)

        def observe_feedback(successful):
            nonlocal outcome
            outcome = successful
            return feedback(successful)

        try:
            screen.action_feedback = observe_feedback
            result = original()
        finally:
            if previous is missing:
                vars(screen).pop("action_feedback", None)
            else:
                screen.action_feedback = previous
        if outcome is False and screen.message:
            # An unavailable manager can return before closing confirmation.
            # Close it so the refusal dialog is the visible modal.
            screen._approach_confirm_open = False
            self.refuse(screen.message, approach=True)
        elif outcome is True:
            self.reason, self.dialog.open, self.inline.rect = None, False, None
        return result

    def handle_event(self, screen, event):
        if getattr(screen, "view_only", False):
            self.dialog.open, self.inline.rect = False, None
            return False
        if self.dialog.handle_event(screen, event):
            if not self.dialog.open and hasattr(screen, "_approach_failure_open"):
                screen._approach_failure_open = False
            return True
        if any(getattr(screen, name, False) for name in ("_approach_failure_open", "_approach_confirm_open", "_fire_popup_open")):
            return False
        return self.inline.handle_event(screen, event)

    def draw(self, screen, surface, original):
        """Draw the native negotiation screen with persistent refusal feedback.

        ``original`` draws the native screen once with no arguments; its return
        value is preserved. Temporary helper overrides capture the response area
        and clip native text before we repaint it with scrollable content. Native
        fields, helpers and clipping are restored even if drawing fails.
        Read-only screens and unrelated native modals use the native draw alone;
        otherwise, an open refusal dialog is drawn above the completed screen.
        """
        # Empty/read-only responses and other native modals must not leave an
        # invisible scroll target behind. Active response drawing sets fresh bounds.
        self.inline.rect = None
        if getattr(screen, "view_only", False):
            return original()
        if any(getattr(screen, name, False) for name in ("_approach_confirm_open", "_fire_popup_open")) or (
                not self.reason and getattr(screen, "_approach_failure_open", False)):
            return original()
        captured = []
        overrides = {}
        clip = surface.get_clip()
        panel_clip, errors = [], []

        def clip_body(surface, rect):
            # Native staff factors and the driver's default hint can paint past
            # their response rectangle. Bound that first pass too, before repainting
            # our scrollable body, and restore the native clip before buttons.
            try:
                panel_clip[:] = [surface.get_clip()]
                surface.set_clip(panel_clip[0].clip(rect))
            except Exception as error:
                errors.append(error)

        def controls(callback, surface, *args, **kwargs):
            if panel_clip:
                try:
                    surface.set_clip(panel_clip[0])
                except Exception as error:
                    errors.append(error)
            return callback(surface, *args, **kwargs)
        # Approach messages stay in their native place above Latest Response.
        if self.reason and not self.approach_refusal and screen.message == self.reason:
            # Keep the full reason in our viewport without letting native inline
            # text enlarge its layout. Restore the original field after drawing.
            overrides["message"] = ""
        if self.reason and hasattr(screen, "_approach_failure_open"):
            overrides["_approach_failure_open"] = False
        if self.kind == "driver":
            header, button = screen._section_header, screen._button

            def section(surface, text, x, y, width, **kwargs):
                result = header(surface, text, x, y, width, **kwargs)
                if text == "Latest Response":
                    # Take the position/width from the native heading. The 22px
                    # scaled gap below matches the native action-button inset.
                    captured[:] = [x, result, width]
                    try:
                        rect = self.ui.pygame.Rect(x, result, width, max(1, screen.submit_rect.y - result - screen.sy(22)))
                        clip_body(surface, rect)
                    except Exception as error:
                        errors.append(error)
                return result

            overrides["_section_header"] = section
            overrides["_button"] = lambda surface, *args, **kwargs: controls(button, surface, *args, **kwargs)
        elif self.reason and not self.approach_refusal or screen.last_interest_breakdown:
            clipped, buttons = screen._draw_clipped, screen._draw_action_buttons

            def text(surface, font, label, *args, **kwargs):
                result = clipped(surface, font, label, *args, **kwargs)
                if label == "Latest Response":
                    try:
                        clip_body(surface, screen._response_rect)
                    except Exception as error:
                        errors.append(error)
                return result

            def actions(surface, *args, **kwargs):
                return controls(buttons, surface, *args, **kwargs)

            overrides.update(_draw_clipped=text, _draw_action_buttons=actions)
        missing = object()
        previous = {name: vars(screen).get(name, missing) for name in overrides}
        try:
            for name, value in overrides.items():
                setattr(screen, name, value)
            result = original()
        finally:
            # Inherited helpers must remain inherited after this single draw;
            # preserve any pre-existing instance overrides and native fields.
            surface.set_clip(clip)
            for name, value in previous.items():
                if value is missing:
                    vars(screen).pop(name, None)
                else:
                    setattr(screen, name, value)
        if errors:
            # Errors from our callbacks occurred inside native drawing. Re-raise
            # only now, so the adapter can identify them as optional UI failures.
            raise errors[0]
        if self.kind == "driver" and captured:
            x, y, width = captured
            rect = self.ui.pygame.Rect(x, y, width, max(1, screen.submit_rect.y - y - screen.sy(22)))
        else:
            rect = getattr(screen, "_response_rect", None)
        factors = screen.last_interest_breakdown
        if rect is not None and (self.reason and not self.approach_refusal or factors):
            self._response(screen, surface, rect, factors)
        if self.dialog.open:
            # The shared dialog only draws supplied text; personnel owns the
            # meaning of the pending decision and which candidate it concerns.
            if self.approach_refusal:
                title, footer = "Approach cannot begin", "No approach was started or personnel slot used."
            else:
                pending = (screen._pipeline_record() or {}).get("status") == "awaiting_player_confirmation"
                title = "Contract cannot be signed"
                footer = "The agreed contract is still awaiting your decision." if pending else "No contract was signed."
            self.dialog.draw(screen, surface, title=title, body=self.reason,
                             subtitle=str(getattr(screen, self.kind).name), footer=footer, font=screen.small)
        return result

    def _response(self, screen, surface, rect, factors):
        """Repaint Latest Response with the refusal and all interest factors.

        ``rect`` is the native panel area captured during screen drawing.
        Staff panels include their heading here; the driver heading is already
        drawn. ``factors`` contains native ``(label, value)`` pairs, displayed as
        signed percentages using the driver threshold or the staff 100-point scale.
        The shared text widget keeps the full content scrollable within the
        remaining panel area.
        """
        theme, pg = self.ui.theme, self.ui.pygame
        pg.draw.rect(surface, self.ui.chrome[theme.PANEL_ALT], rect, border_radius=max(1, screen.sy(6)))
        pg.draw.rect(surface, self.ui.chrome[theme.BORDER], rect, max(1, screen.sy(1)), border_radius=max(1, screen.sy(6)))
        if self.kind == "staff":
            pad = screen.ui_gap(10, minimum=4)
            heading = pg.Rect(rect.x + pad, rect.y + pad, rect.width - 2 * pad, screen.small.get_linesize())
            screen.draw_text_block(surface, "Latest Response", screen.small, heading, (240, 240, 240))
            rect = pg.Rect(rect.x + 1, heading.bottom + screen.ui_gap(4, minimum=2), rect.width - 2,
                           max(1, rect.bottom - heading.bottom - screen.ui_gap(4, minimum=2) - 1))
        blocks = [(self.reason, theme.NOTICE_TEXT), ("", theme.TEXT_MUTED)] if self.reason and not self.approach_refusal else []
        threshold = max(1.0, screen._interest_threshold()) if self.kind == "driver" else 100.0
        blocks.extend((f"{value / threshold * 100:+.0f}%  {label}", theme.SUCCESS if value >= 0 else theme.DANGER)
                      for label, value in factors)
        self.inline.draw(screen, surface, rect, blocks, screen.small)
