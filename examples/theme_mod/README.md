# Theme example architecture

This Python source mod uses API `0.3.0` and the `ui-settings` permission to
connect an Options editor to a restart-only Color Pack palette. The launcher
loads its source through the shared trusted runner.

## Registration and saved values

`src/mod.py` implements the runner's `WitWorld` lifecycle. Activation declares a
Theme tab with labelled colour fields through `register_settings`, then expands
the returned saved values or defaults into a Custom Theme palette through
`register_theme`. A successful native save delivers `ui-settings.changed`;
the event handler republishes that values document as the staged palette.
The native theme adapter keeps the running session's palette fixed until restart.

The launcher owns `settings.json` inside the installed mod folder. The shared
native Options adapter owns hex inputs, live swatches, draft state, validation
and Save/Reset actions. Inputs accept hex digits with or without a leading `#`;
saved values use uppercase hex with `#`. The example supplies field declarations and palette
data through the [implemented settings API](../../hook_manager/README.md#ui-settings-and-palettes).

## Palette mapping

`src/palette.py` maps all 59 named `UITheme` colour roles to 52 editable groups.
Each `COLOR_GROUPS` record contains a stable number, a default RGB or RGBA tuple
and the native roles coupled to that colour. Editor fields use `colour_<number>`
IDs and role-name labels such as **Text / Selection Text** for group 18.
Labels replace underscores with spaces, capitalize each word and join coupled
roles with ` / `. Display labels do not change saved IDs or the palette schema.
Palette expansion copies a group's value to
every role in that record; equal edited values never merge groups.

| Colour number | Coupled native roles |
| --- | --- |
| 11 | `PRIMARY`, `SELECTION_BORDER` |
| 18 | `TEXT`, `SELECTION_TEXT` |
| 20 | `TEXT_DIM`, `DISABLED_TEXT` |
| 43 | `MARKET_CARD`, `TABLE_ROW` |
| 44 | `MARKET_CARD_ALT`, `TABLE_ROW_ALT` |
| 45 | `MARKET_HEADER`, `TABLE_HEADER` |
| 46 | `MARKET_BORDER`, `TABLE_GRID` |

The remaining 45 groups each contain one role. Groups 1-51 use RGB; group 52 is
the RGBA `OVERLAY`, with default `#000000A0`. `PALETTE_SCHEMA = 1` identifies the
saved mapping. Explicit group numbers and membership determine field identity,
independently of display order and edited values. Palette changes require an
explicit mapping and saved-schema review.

The native adapter applies the selected palette to named theme roles, imported
aliases and chrome lookup. Already-resolved colours bypass further translation.
Typography, artwork, arbitrary drawing literals and computed colours are outside
the example's named-role mapping.
