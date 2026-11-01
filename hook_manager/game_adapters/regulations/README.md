# Regulation API and game integration

Source API `0.4.0` mods with the `regulations` capability can validate supported
Formula actions, add rule categories and influence rule changes. The adapter
selects the game actions; mods supply declarations and Python handlers rather
than choosing native methods.

Declarations live in the selected pack's root `regulations.json`, under
`mod_extensions["your.mod.id"]`. Each enabled section requires `"schema": 1`.
The [definition reference](../../../launcher/regulations/README.md#selected-json-structure)
describes all declaration fields and limits. Disabled mods' sections are ignored.

## Validation handlers

A validator declaration names a rule, supported scope and function:

```json
{
  "id": "rookie_length",
  "rule": "example.rookie_contracts.length",
  "scope": "personnel.contract",
  "handler": "mod.validate_contract"
}
```

This belongs in the owning mod's `validators` array. The rule must be a native
category or one declared by that mod; the handler must belong to its source.

| Scope | Actions and data |
| --- | --- |
| `personnel.approach` | [Driver/staff approaches](../personnel/README.md#approaches-personnelapproach) to Formula teams. |
| `personnel.contract` | [Shared terms](../personnel/README.md#contracts-personnelcontract) for [drivers](../drivers/README.md), [staff](../staff/README.md) and [academy memberships](../academy/README.md). |
| `development.project` | [Aero and chassis projects](../projects/README.md). |

Handlers are synchronous functions, separate from `WitWorld` lifecycle methods.
They receive fresh dictionaries and return a dictionary:

```python
def validate_contract(context, regulation):
    option = regulation[regulation["selected"]]
    proposal = context["proposal"]
    native_checks = proposal.get("native_checks", {})
    limit = option["parameters"]["max_years"]
    if (native_checks.get("rookie_contract_length_applies")
            and limit is not None
            and proposal["proposed"]["duration_weeks"] > 52 * limit):
        return {"decision": "deny", "message": "Contract is too long."}
    return {"decision": "allow"}
```

The [rookie contract example](../../../examples/rookie_contract_mod) supplies
the matching category and native-cap switch. Denial messages need 1–160 printable
characters without surrounding whitespace. Handler responses are dictionaries,
unlike `on_event()` responses, which are JSON strings.

An allowance permits the game's remaining checks to run. A denial stops the
action with its reason. If a required check cannot run, the action stops as
unavailable. An offer preview never approves the later signing.

## Common context and rule data

| `context` field | Meaning |
| --- | --- |
| `schema` | Integer `1`. |
| `scope` | One of the supported scopes above. |
| `operation` | `"preview"` for driver/staff offer submission; `"commit"` for final action checks. |
| `initiator` | `"player"`, `"ai"`, `"system"` or `"unknown"`. |
| `clock` | Career `season` and `absolute_week` when prepared. |
| `effective` | Proposed `season`, `start_week` and `end_week`; unavailable dates are `None`. |
| `proposal` | Action-specific data described by the linked adapters. |

`regulation` supplies `current`, optional `upcoming`, and `selected`.
Use `regulation[regulation["selected"]]` to read the option applying to the
proposal's effective season. Its `parameters` contain rule values;
`option_data` holds extra data declared by the mod for that option.
Do not assume that today's rule applies to a future contract or project.

The combined context and rule data must fit 4,096 UTF-8 bytes, 1,024 nodes and
eight container levels. Action preparation and validation share a 100 ms
deadline. See the [execution limits](../../../launcher/regulations/README.md)
for other bounds.

## Categories and changes

An owned category declares a `key`, `label`, `default` and `options`.
Its key starts with the mod ID and `.`. Options have consecutive positive
integer IDs, unique keys, names and optional parameters. Loaded categories appear
on the game's regulation screen. Saved definitions must match; changing a
definition does not overwrite a saved career's rules.

The mod's `option_data` can attach extra JSON data to native or owned options
without replacing native fields. `scripted_changes` schedules owned options
for specific seasons.

An owned category can instead use automatic changes:

```json
"change_control": {
  "mode": "automatic",
  "base_chance": 0.25,
  "announcement_week": 40
}
```

The chance is checked once per season. Week indexes run from 0 to 51, so
`40` means displayed week 41. Selected alternatives apply next season and are
announced through the inbox. Existing scripts and approved changes take priority.
This needs no Python voting handler. Progress and pending notices are saved
with the career.

## Native rule switches

The mod section's `native_rules` accepts `rookie_contract_length_check` and
`staff_prestige_check`. Omitted or `true` keeps the named native check;
`false` requests a bypass. Other game restrictions still apply.

A switch can follow a boolean parameter in an owned category:

```json
{
  "rookie_contract_length_check": {
    "rule": "example.rookie_contracts.length",
    "parameter": "native_check"
  }
}
```

Every option must supply that boolean. The proposal's contract season selects
the value. Only one enabled mod can own each switch. The
[driver](../drivers/README.md) and [staff](../staff/README.md) adapters explain
what each switch affects.

## Voting handlers

A mod section can replace teams' voting calculations:

```json
"voting": [{"rule": "drs_policy", "handler": "mod.vote"}]
```

Use the exact native or owned category key, as in the
[DRS voting example](../../../examples/regulation_vote_mod).
A synchronous `vote(context, regulation)` function returns
`{"choice": "current", "reason": "Keep the existing rule."}` or the same shape
with `"choice": "proposed"`. Reasons must be nonblank and no longer than
2,048 UTF-8 bytes.

Voting context includes `team`, a frozen `snapshot`, the original `native`
evaluation, decision/effective seasons, a repeatable `seed`, and review/proposal
IDs. Compare IDs without depending on their format. The rule argument supplies
`key`, `current` and `proposed` options, including declared `option_data`.

Only one mod can replace a category's calculation. Automatic categories and
calendar, technical-era and entry-limit categories cannot use these handlers.
The game still selects voters, records player ballots, counts votes and saves
reviews. Player analysis does not cast the player's vote. Failed batches record
no partial mod results.

## Internal responsibilities

Game-specific planners prepare proposals without applying them.
`RegulationGuards` controls the native action; `RegulationPolicy` selects
options for its season. `RegulationChannel` carries requests to the external
launcher, which executes Python inside Wasm. Live game objects never reach mods.

Category, change and voting adapters connect declarations to native loading,
screens, scheduling, inbox and saved voting records. Automatic-change load/save
support retains progress even when mods are disabled.

Trusted setup checks compatibility and required coverage before activation.
Each career owns its rule data and action wrappers. Retirement waits for active
checks before restoring owned changes; the next career needs fresh preparation.
These modules expose no direct native-call API, and importing them installs nothing.
