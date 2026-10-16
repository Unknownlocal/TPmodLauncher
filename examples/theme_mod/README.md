# Custom theme example

This source mod adds a **Theme** tab to the game's Options screen through the
shared [UI settings API](../../hook_manager/api/0.3.0/README.md). Copy this folder
into the selected Formula data pack and restart the game through the launcher.

Each row has a hex input and a live colour swatch. Click a row to edit it; Tab and
Shift+Tab move between rows. Ctrl+A selects its text, Ctrl+V pastes a colour, and
Enter saves. Mouse-wheel scrolling reaches all 52 rows. The overlay's swatch uses
a checkerboard to show transparency. Invalid entries disable Save.

**Save** writes `settings.json` inside the installed mod folder. **Reset to
Defaults** stages defaults; Save keeps them. Unsaved edits survive tab switches
within the options screen. Back leaves the screen without saving those edits.

After saving, select **Custom Theme** in the existing **Color Pack** dropdown
under **Graphics**, confirm **Save choice**, and restart the game through the
launcher. The selected palette loads from this mod's saved settings on startup.
Further colour edits also take effect after restarting, matching the game's
built-in themes. Selecting a built-in pack restores its usual behaviour on the
next launch. Without this mod, the game falls back to Default Blue.

Theme numbering and coupling are defined by this example. The `ui-settings`
permission covers both the editor and palette registration through separate
operations. No colour picker is included yet.

The palette covers all 59 named colour entries in the inspected game's `UITheme`,
grouped into 52 distinct default colours. Typography settings, caches and computed
rating colours are not separate editable entries.

Each `COLOR_GROUPS` record contains a stable number, the default RGB or RGBA tuple,
and all native theme roles coupled to that colour. The editor labels these
records **Custom Theme Colour 1** through **Custom Theme Colour 52**, with a hex
input and swatch beside each row. Native role names are internal metadata.

The coupled pairs are:

| Colour number | Native roles |
| --- | --- |
| 11 | `PRIMARY`, `SELECTION_BORDER` |
| 18 | `TEXT`, `SELECTION_TEXT` |
| 20 | `TEXT_DIM`, `DISABLED_TEXT` |
| 43 | `MARKET_CARD`, `TABLE_ROW` |
| 44 | `MARKET_CARD_ALT`, `TABLE_ROW_ALT` |
| 45 | `MARKET_HEADER`, `TABLE_HEADER` |
| 46 | `MARKET_BORDER`, `TABLE_GRID` |

The remaining 45 groups each contain one role. Groups 1-51 use `#RRGGBB` inputs.
Group 52 (`OVERLAY`) uses `#RRGGBBAA`; its default is `#000000A0`.

## Stable numbering

`PALETTE_SCHEMA = 1` identifies this mapping. Initial numbers follow the first
appearance of each unique default colour in the inspected theme. The explicit
numbers are the identities for future saved settings; display order and edited
hex values must never determine identity.

Changing one group will update every native role listed in that group. Giving
two groups the same custom hex must not merge them. Existing numbers and coupled
memberships stay fixed; future game palette changes require an explicit mapping
review and, if necessary, a schema migration. New groups receive new numbers.

## Inspection baseline

Compared against the installed `TeamPrincipal.exe` on 2026-10-16, SHA-256:

```text
dbc18d2fe4ec24d4039104a40dd43fa948004c59823b0309baf7849f02ee6ef2
```

The baseline comparison checked exact role coverage, default channel values,
unique group numbers and colours, and shared-colour membership. It read the
packaged theme without starting the game or changing its settings. The mapping
defines colour identities. The native adapter applies the selected colours to
`UITheme`, imported native UI colour aliases and the management chrome lookup.
Already-resolved colours bypass that lookup, so equal custom and default colours
cannot cause a second translation. Computed colours, artwork and arbitrary
literal drawing colours are not individually editable theme roles.
