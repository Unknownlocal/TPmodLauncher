# Game adapters

The adapters connect supported Formula game actions to mod regulation checks.
They prepare copied data, ask the launcher to run the mod's Python handler, and
allow or refuse the action. The game's remaining checks still apply.

These modules run inside the game as trusted launcher code. Mods use the
documented data below through source API `0.4.0` with the `regulations`
capability; they cannot import these modules or call game functions.

## API areas

| Area | What mods can check |
| --- | --- |
| [Personnel](personnel/README.md) | Driver/staff approaches, submitted offers and shared contract data; optional payroll totals. |
| [Drivers](drivers/README.md) | Driver signings and renewals, including rookie contract limits. |
| [Staff](staff/README.md) | Team/supplier employment and contracts prepared during team staffing. |
| [Academy](academy/README.md) | Membership signings and renewals. |
| [Projects](projects/README.md) | Aero projects, specification upgrades and current/future chassis projects. |
| [Regulations](regulations/README.md) | Common handler contract, rule selection, custom categories, changes and voting. |

[Shared UI helpers](ui/README.md) support refusal messages; they expose no mod API.

## How the parts fit together

Driver, staff, academy and project planners calculate proposed terms without
applying the action. Personnel support handles shared negotiations and feedback.
The regulation package adds the scope, initiator and operation, selects the rule
for the proposal's season, and sends the request to the launcher.

The external [regulation loader](../../launcher/regulations/README.md) checks
declarations and retains rule data. The [mod runtime](../../mod_runtime/README.md)
executes handlers inside Wasm. Only copied descriptions cross into mod code.

Shared record helpers keep related draft records linked and check relevant inputs
before applying changes. Validation-operation support keeps a failed required
check from being mistaken for success, including in supported game worker calls.

The launcher's fixed source bundle supplies these modules. Importing them installs
nothing: trusted setup checks compatibility, owns installation and restores its
changes during cleanup. Packs cannot add native targets or adapter sources.
