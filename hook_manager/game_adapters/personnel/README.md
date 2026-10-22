# Shared native personnel negotiation support

Driver and staff employment share the game's offer execution, uncontested signing
and player confirmation methods. This package supplies one caller catalogue and
one `PersonnelCallers` instance for those methods, preventing competing wrappers.
It imports no regulation dispatcher, mod definitions, launcher or sandbox runtime.

The consuming `PersonnelAdapter` lives under `../regulations/`. It owns rule
selection and validation; this package owns native preparation and cancellation.
Driver-specific query isolation remains in `../drivers/queries.py`. Common contract
formatting remains in `../employment.py`; the two native planners stay separate.

`caller_targets.py` includes the shared methods plus driver-specific proposal and
query methods when driver support is active. `preparation_methods.py` checks their
exposed call signatures. Inspected staff commitment and prestige-skill queries
are read-only; team prestige getters can initialize
fields, which `preparation.py` holds until signing is allowed.

`callers.py` copies only offer/context data, preserving aliases. The staff branch
can also set `_oval_target_entry_id` even in Formula mode: when such a hint is
present, it uses a shallow staff copy for the pre-sign caller. The signing guard
restores the real staff argument, then publishes the hint only on allowance.
An ordinary native precheck failure retains the same hint/getter initialization
the game would have performed. Mod refusal discards them.

Player acceptance treats mod refusal differently from native `False`: native
`False` tries backups and closes the decision, while refusal must return its reason
with the decision still pending. `PersonnelCancellation` unwinds only that checked
acceptance path. Explicit declines retain native cleanup and backup behavior.

`feedback/` owns optional driver/staff negotiation presentation separately from
signing. It observes ordinary screen imports and checks native helper signatures;
it never imports a screen to install hooks. Each kind installs its UI wrappers as
one group. UI incompatibility reports through the launcher reporter and leaves
the signing integration usable. Shutdown restores both owners.

A regulation refusal opens a dialog following the native driver refusal layout.
The widgets and common native bindings come from [shared UI support](../ui/README.md).
Personnel supplies contract wording, candidate names and pending-decision notes;
its own bindings check the negotiation-specific helpers and layout anchors.
The full reason also stays in Latest Response after dismissal, until a later
native action changes the response. Native wrapping, fonts, theme and scrollbar
helpers keep both the dialog body and inline response within fixed rectangles.
All interest factors remain reachable by scrolling, rather than retaining the
native nine-row limit or dropping rows at the panel edge. Native action controls
keep their positions. Feedback is held by weak screen references; no UI methods,
objects or registration API are exposed to mods. Academy feedback is separate.

Enclosing market batches, recovery, takeover/merge, load/activation/rollover and
academy behavior remain separate coverage gates. This package installs nothing
on import and grants no callable-target access to mods.

## Remaining caller boundaries

Native caller inspection and owned-code prototypes establish these remaining
boundaries; they do not install transaction handling or open personnel admission.

| Caller | Behavior the enclosing integration must handle |
| --- | --- |
| `StaffManager.onboard_joining_team_staff` | Creates free staff/history before signing; returns successful names. A rejected signing can leave generated people behind. |
| `StaffManager.ensure_active_teams_have_staff` | Seeds missing staff, ignores the signing boolean and returns `None`. Generation/selection must be prepared before required staffing is admitted. |
| `StaffManager.ensure_active_suppliers_have_head_of_engine` | Requests supplier approaches through personnel negotiation; it does not directly create/sign replacement staff. |
| `PersonnelNegotiationManager.resolve_due_decisions` | Normalizes/ranks offers, tries alternatives, then closes the market and removes its deadline. Native signing failure, policy denial and unavailable validation need distinct caller outcomes. |
| `PersonnelNegotiationManager.ai_manage_staff` | Ignores uncontested signing results; a denied acceptable offer stays open and can be retried on later invocations. |
| `PersonnelNegotiationManager.ai_manage_drivers` | Reserve offers can sign immediately; main-driver final offers wait for deadline resolution. Preserve native failure reasons and fallback/cooldown behavior. |

Team recovery loops are finite in the inspected code, although the same unsigned
candidate can be attempted for multiple teams. A policy-denied offer may have a
legal alternative; unconditionally aborting every fallback would suppress it.
Unavailable validation must remain distinguishable without matching reason text.

Recovery is also called after staff load, and joining-team onboarding runs after
season changes during rollover. Local signing cancellation alone cannot guarantee
whole-load or whole-rollover admission. [Staffing preparation](../staff/README.md)
now captures native onboarding/recovery choices with canonical existing/generated
person refs and private generation/history. Publication, stale-plan checks, outer
transactions and worker acceptance remain to be implemented and verified.
