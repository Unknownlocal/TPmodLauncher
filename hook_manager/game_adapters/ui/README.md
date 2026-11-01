# Shared UI helpers

This package supplies private drawing and dialog helpers for trusted game
adapters. It exposes no mod API. Public colour settings and palette operations
are described in the [settings API](../../README.md#ui-settings-and-palettes).

## Main parts

- `ui_bindings(ScreenClass)` checks and returns the game's loaded drawing,
  theme, text-wrapping and scrollbar helpers.
- `ScrollText` wraps text, draws visible rows and handles scrolling within a
  supplied rectangle. It keeps drawing inside that rectangle.
- `ScrollableDialog` adds a dismissible dialog with a scrollable body,
  caller-supplied text and an error or information style.

Consumers choose the wording, when a dialog opens and what closing it means.
They forward input to the helpers and retain their own screen state.
The [personnel adapter](../personnel/README.md) uses them for refusal messages.

Each consumer checks its screen-specific compatibility. Importing this package
loads no game modules and installs no hooks.
