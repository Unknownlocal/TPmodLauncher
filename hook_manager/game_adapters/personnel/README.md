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
with the decision still pending. `PersonnelCancellation` carries the trusted
outcome through checked caller boundaries. Policy refusals retain the existing
player-acceptance cancellation; explicit declines keep native backup behavior.

Deadline resolution and staff/driver AI also run through caller hooks. Policy
denial returns the ordinary signing failure value so native ranking, alternative
offers and cooldown calculation remain in charge. Driver AI failure classification
receives a trusted generic reason for a policy-denied offer, so phrases in mod
messages cannot create native permanent or role-specific eligibility blocks. The
full mod message is retained on the failed record; native failure debug output
uses the generic policy marker. Attribution is limited to the just-completed row
and consumed once. Genuine native failures keep their original classification.
If required validation is
unavailable, cancellation passes through nested uncontested signing to the owning
caller, which retains its native `None` return. This stops later attempts and
post-sign failure/closing logic; it does not turn unavailability into a losing
offer. A direct offer call still returns its native boolean and exposes the
structured validation outcome separately.

`MarketPreparation` gives each outer caller one private native manager/state view.
Market records, deadlines, proposals, pending confirmations and mailbox data are
copied together by `MarketGraph`, retaining aliases such as an offer shared with
its context. Native closing, confirmation staging and AI failure handling publish
completed candidate work. Later unavailability discards pending preparation while
keeping those checkpoints. Existing records retain their diagnostic refusal text;
an unpublished new record is not created solely to store that text.

Publication preserves canonical dictionary/list identities and separates newly
published containers from the working view. Checkpoints publish the completed
candidate's stores and individual records the game has failed or cancelled,
including driver backups released by a filled seat. Other offers for those backup
candidates stay private. This also retains staff rejections made inline by native
AI code, without needing another completion helper. Market cancellation excludes
the blocked offer; earlier rejections by other teams remain complete, even for the
same person. A player-confirmation decision excludes that person's whole group,
including an explicit decline awaiting a backup.
Only native deadline removals for the completed records are retained;
normal caller completion performs a final publication. Native mailbox methods run
on a private owner, including mail sent by live native signing. `MarketMail` records
which row sent a message while native notification, debug, offer and completion
helpers run. A checkpoint publishes that row's messages and urgent-mail counter
contribution. Pending notifications, edits to messages from before this view and
mail outside those helpers wait for normal publication. Native save calls through the view
publish before saving.
Player-decline relationship adjustments wait for completion, so an unavailable
backup validator cannot leave a penalty behind with a still-pending confirmation.
Native code still calculates the adjustment and its existing error handling is
retained.

This protects market preparation; allowed native contract/financial effects still
run on real people and teams. It does not make an enclosing week, load or rollover
atomic. Repeated-proposal suppression and outer career/worker admission retain
their later gates. No offer-selection algorithm is reimplemented here.

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
| `PersonnelNegotiationManager.resolve_due_decisions` | Native ranking/fallback remains active. Candidate checkpoints keep completed records, mail and deadline cleanup when later unavailability discards remaining preparation. |
| `PersonnelNegotiationManager.ai_manage_staff` | A policy-denied acceptable offer keeps native continuation and can be retried later. Unavailability now unwinds the ignored uncontested result before later approach passes. |
| `PersonnelNegotiationManager.ai_manage_drivers` | Reserve offers can sign immediately; main-driver final offers wait for deadline resolution. Unavailability stops before failure handling. Policy denial uses ordinary native cooldown without inferring eligibility from mod prose; genuine native refusal retains native classification. |

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
