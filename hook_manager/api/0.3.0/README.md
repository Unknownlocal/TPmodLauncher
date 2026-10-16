# UI settings API

API `0.3.0` adds the `ui-settings` capability. Python source mods use
`wit_world.register_settings(definition)` and `wit_world.save_settings(values)`.
Both accept and return JSON strings bounded to 4,096 UTF-8 bytes. This replaces
the unreleased theme-specific contract; no `theme-settings` aliases are retained.

## Declare a settings tab and load values

`register_settings` accepts one tab definition per mod:

```json
{"schema":1,"label":"Widget Options","fields":[{"id":"accent","label":"Accent Colour","color":"#3070B9"},{"id":"overlay","label":"Overlay","color":"#000000A0"}]}
```

`schema` is the mod's settings schema, a positive integer at most 65,535. `label`
is the tab title. `fields` contains 1-64 typed field declarations in display order.
Each field has a stable `id`, its display `label`, and one typed default member.
The first supported type is `color`, whose value is a `#RRGGBB` or `#RRGGBBAA`
default. Unsupported types are rejected until implemented. The typed member keeps
declarations small enough for all 52 labelled fields in the theme example.

Field IDs contain 1-64 ASCII characters, starting with a lowercase letter and
continuing with lowercase letters, digits, `.`, `_`, `:` or `-`. IDs must be
unique within their mod. Tab and field labels contain 1-64 printable characters
without surrounding whitespace. Mods choose their own labels; the shared API
does not generate theme names, group colours or require distinct defaults.
Callbacks, filenames and game targets are not accepted.

The result contains saved values, or defaults if `settings.json` is absent:

```json
{"schema":1,"values":{"accent":"#3070B9","overlay":"#000000A0"}}
```

Registration creates no file. An identical declaration can be registered again
to read current values. Changing the definition during a session fails. Invalid,
oversized or schema-incompatible existing settings fail registration without
overwriting the file. Migrations are not automatic.

## Save values

`save_settings` accepts a complete values document and returns its normalized
contents after an atomic save. Every registered field ID must appear exactly
once; unknown IDs are rejected. Colour values include `#`, accept either letter
case and are returned in uppercase. RGB defaults require six digits; RGBA
defaults require eight. Equal default or edited values remain independent fields.
Saving the declared defaults implements reset without a separate operation.

The host binds each mod to its installed directory and the fixed `settings.json`
filename. Guests cannot select paths or another owner's store. Reads and writes
use pinned ordinary directories and reject linked, shared and special settings
files. Atomic replacement retains the previous document and in-memory values if
a write fails. Windows operations stay relative to the pinned directory handle.

## Ownership and integration

At most four mod settings tabs may register per process, with distinct tab titles.
The host supplies ownership; failure or closure removes declarations while keeping
saved files. Calls require `ui-settings`, including during source import. API
`0.2.0` remains supported with its existing permissions. The prepared runner uses
private ABI `0.2.0`.

The host stages copied typed fields, including defaults and current swatch values.
Native tab rendering and settings-change event delivery remain subsequent UI
integration work. The theme mod will consume these settings; registering or
saving settings does not register a palette or change game colours. Palette
application belongs to a separate adapter.
