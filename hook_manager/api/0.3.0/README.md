# API 0.3.0 architecture

[`mod.wit`](mod.wit) defines the public `code-mod` component world. It retains
the `activate` and `on-event` lifecycle exports, diagnostics and menu operations,
and adds settings and palette operations under the `ui-settings` feature.
Feature selection controls generated bindings; the launcher separately checks
manifest grants and component signatures at admission.

## Guest and host boundary

Mods with prebuilt Wasm components implement the public WIT contract directly.
Python source mods use the trusted runner's `wit_world` facade and private ABI.
Both paths reach the same host providers through bounded scalar calls and JSON
strings. The host supplies identity and permissions; declarations contain data
rather than callbacks, filesystem paths or executable game targets.

## Settings ownership

`launcher/ui_settings.py` owns definitions, validation and mod-local storage.
Registration binds a labelled tab to typed colour fields and returns saved
values or defaults. `_settings_files.py` implements pinned-directory access and
atomic replacement of the fixed `settings.json` file. Persistent values outlive
the guest instance; tab registrations remain owned by its session.

`hook_manager/settings.py` owns the native Options editor and unsaved drafts.
Save requests cross the bridge to `launcher/pack_mods.py`, which validates the
owner and generation, persists the values and acknowledges success or failure.
A successful native save then delivers `ui-settings.changed` to its owner.
Request IDs and registry generations reject duplicate or stale work.

## Palette ownership

Palette registration is separate from settings persistence, sharing the
`ui-settings` grant. `hook_manager/themes.py` owns declarative palette validation
and staged records in the launcher, and native adaptation inside the game.
The adapter supplies owned Color Pack choices, checks native colour coupling
and applies the selected palette at startup. It updates `UITheme`, imported
aliases and chrome lookup while preserving restart-only selection behaviour.
Cleanup restores owned changes without deleting saved settings or theme choices.

The [hook manager API overview](../../README.md#ui-settings-and-palettes)
describes the implemented operations, JSON formats and limits.
