# Private game adapters

This package contains the launcher's game-specific support. Mod source runs in
Wasm and cannot import these modules or select native game functions. Importing
an adapter defines its code; trusted setup owns installation and removal.

## Main parts

- `drivers`, `staff` and `academy` prepare proposed contracts and handle the
  game-specific calculations and records.
- `personnel` handles shared negotiations, working market records and feedback.
- `projects` prepares aero and chassis development proposals.
- `ui` supplies shared game UI bindings, scrollable text and dialogs.
- `regulations` adds rule selection, validator contexts and communication with
  the launcher to those game-specific calculations.
- `native_record_graph.py` shares record copying and publication between
  negotiations and staffing. `native_values.py` describes record contents and
  identities so callers can detect changes before applying their updates.
- `validation_operation.py` keeps required checks available throughout an
  admitted game action. Nested calls share failure state; each adapter retains
  its own worker permission. `ValidationCancellation` carries a refusal to the
  responsible caller, so an unfinished action cannot be treated as successful.

Planners return `clock`, `effective` and `proposal` data without applying the
proposed action. Native regulation data can still affect the game's seat or
project capacity. Ordinary mod validators receive copied descriptions rather
than live game objects. A successful mod check still leaves the game's own
remaining checks in place.

The direction is `regulations -> game-specific support -> callable/manager`.
Internal names such as `drivers.sign` identify trusted targets; they are not
mod declarations. The external `launcher/regulations` package owns enabled-mod
JSON definitions and stored rule data. `mod_runtime/_regulations` executes the
mod's Python handlers inside Wasm.

## Loading and ownership

`launcher/game_bridge.py` carries a fixed list of adapter sources. The trusted
`adapters` operation asks `hook_manager/adapter_modules.py` to load that bundle
under the bridge's private module name. Each module keeps its own functions and
variables, so helpers with the same name cannot replace one another. Relative
imports use only bundled definitions; the manager and callable classes are
shared with the running bridge.

Loading uses a temporary importer and leaves the game's file search path alone.
All bundled definitions load once; later requests reuse them. A failed load
removes its partially loaded modules and reports a failed status. Shutdown
removes only definitions still owned by that bridge. Packs and control requests
cannot add sources to the bundle.

Loading definitions alone installs no hooks or enables validation. Trusted
startup performs those steps after checking the enabled mods and selected career.

See [driver support](drivers/README.md), [staff support](staff/README.md),
[academy support](academy/README.md), [project support](projects/README.md),
[shared UI support](ui/README.md) and
[regulation integration](regulations/README.md).
