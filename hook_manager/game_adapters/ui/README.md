# Shared private UI components

Trusted game adapters can reuse `ui_bindings`, `ScrollText` and `ScrollableDialog`.
Importing this package installs no hooks and imports no game modules. These are
launcher helpers, with no new mod-facing API or callable registration.

`ui_bindings(ScreenClass)` checks the shared screen/helper signatures and returns
already-loaded native drawing, theme, wrapping and scrollbar support. The consumer
calls it after its screen and native dependencies have imported normally. Checks
use paths and signatures; matching implementations are accepted without hashes.
Screen-specific signatures and compatibility reporting belong to the consumer.

`ScrollText(ui).draw(screen, surface, rect, blocks, font)` draws `(text, color)`
blocks within the supplied rectangle. It caches wrapped lines, draws visible rows,
clamps scrolling after layout changes and restores the surface clip. Forward input
through `handle_event(screen, event)`. Wheel movement uses the font supplied to the
last draw, and the native scrollbar handles track clicks and thumb dragging.

`ScrollableDialog(ui)` supplies a dismissible dialog with a scrollable body. Its
`draw` takes caller-owned `title` and `body`, optional `subtitle` and `footer`, an
`error` or `info` style, and an optional body font. Without a font it requests the
screen's standard 22px UI font. Set `open` to show it and forward input through
`handle_event`; consumers own when to open it and what closing means.

For example, inside a trusted adapter whose native UI is ready:

```python
ui = ui_bindings(ScreenClass)
dialog = ScrollableDialog(ui)
dialog.open = True
dialog.draw(screen, surface, title="Schedule updated", body=summary, style="info")
```

The personnel adapter supplies contract wording, candidate names, pending-decision
notes and persistent refusal state. Its native layout anchors, clipping boundaries,
wrapper ownership and event lifecycle stay in `personnel/feedback`.
