# Academy membership support

This package describes Formula academy agreements and supports checks around
signing and renewal. Academy membership has an annual fee, separate from a
driver's employment salary. It uses the game's existing calculations and keeps
proposed changes separate from the live career.

## Main parts

- [planner.py](planner.py): `AcademyContractPlanner` describes the driver, academy,
  existing agreement and proposed terms. It calculates signing costs and term
  limits without signing, renewing or charging fees.
- [terms.py](terms.py): converts membership seasons into start and end weeks using
  the game's calendar. A renewal retains the original start and annual fee.
- [preparation.py](preparation.py): gives AI callers temporary copies of academy
  records and random-number state. Allowed actions apply their preparation before
  the game's signing method runs; refused actions discard remaining preparation.
- [callers.py](callers.py): connects player and AI actions to that preparation and
  retains player refusal messages. [feedback.py](feedback.py) draws the temporary
  refusal notice on the academy screen.

## Integration

The [regulation adapter](../regulations/README.md) owns rule selection and mod
checks. It wraps academy signing and renewal while leaving the game's own
eligibility checks, fees and record updates in charge after mod approval.

This package checks game method names and the arguments they accept before use.
The regulation adapter reports incompatibility and skips the affected
integration. Importing the package installs nothing. These are private launcher
modules; mods cannot select or call game functions through them.
